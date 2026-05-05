import os
import sys
import json
import time
import socket
import threading
import subprocess
import re
import struct
import argparse
import socketserver
from pathlib import Path
from typing import Dict, Any, Optional, List

# 경로 설정 및 모듈 임포트
CORTEX_DIR = Path(__file__).resolve().parent
WATCHER_SCRIPT = CORTEX_DIR / "watcher.py"
SCRIPTS_DIR = str(CORTEX_DIR.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.logger import get_logger

# 서버는 직접 파일에 로그를 남겨야 함 (ctl이 종료된 후에도 유지되도록)
logger = get_logger("server")

# IPC: TCP 소켓 (Windows 호환)
ROUTER_HOST = "127.0.0.1"
ROUTER_PORT = 42384
WORKER_HOST = "127.0.0.1"
WORKER_PORT = 42385


def get_idle_timeout() -> int:
    try:
        from cortex.indexer_utils import load_settings
        project_dir = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
        settings = load_settings(project_dir)
        rules = settings.get("indexing_rules", {})
        timeout = rules.get("idle_timeout") or settings.get("idle_timeout")
        if timeout is not None:
            return int(timeout)
    except Exception:
        pass
    return 300

IDLE_TIMEOUT = get_idle_timeout()



# ==========================================
# 소켓 통신 유틸리티
# ==========================================
def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(min(n - len(data), 4096))
        if not chunk:
            return None
        data += chunk
    return data

def recv_msg(sock):
    header = recv_exact(sock, 4)
    if not header:
        return None
    size = struct.unpack("!I", header)[0]
    data = recv_exact(sock, size)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))

def send_msg(sock, msg):
    data = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)


# ==========================================
# 1. 워커(Worker) 모드 (PyTorch 및 모델 로드 전담)
# ==========================================
def run_worker():
    import torch
    current_device = "cpu"
    if torch.cuda.is_available():
        current_device = "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        current_device = "mps"
    elif hasattr(torch, "xpu") and torch.xpu.is_available():
        current_device = "xpu"

    model = None
    model_load_error = None

    def _load_model_bg():
        nonlocal model, model_load_error
        try:
            from vector_engine import _load_model
            logger.info(f"[Worker] Background model loading started on {current_device}...")
            # 실제 모델 로딩 수행 (시간이 걸리는 작업)
            model = _load_model(device=current_device)
            logger.info(f"[Worker] Model loading complete. Engine Ready on {current_device}.")
        except Exception as e:
            import traceback
            model_load_error = str(e)
            logger.error(f"[Worker] Background loading failed: {e}\n{traceback.format_exc()}")

    # [Root Cause Fix] 모델 로딩 전에 소켓 서버부터 바인딩 및 리슨 시작
    # 이를 통해 라우터가 즉시 연결(Connect)에 성공하여 'Worker failed to start within timeout'을 방지함
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((WORKER_HOST, WORKER_PORT))
    server.listen(5)
    logger.info(f"[Worker] Server listening on {WORKER_PORT}. Initializing model in background...")

    # 백그라운드 로딩 스레드 시작
    threading.Thread(target=_load_model_bg, daemon=True).start()

    try:
        while True:
            server.settimeout(1.0)
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue

            try:
                request = recv_msg(conn)
                if not request:
                    continue
                cmd = request.get("command", "embed")

                if cmd == "ping":
                    if model_load_error:
                        send_msg(conn, {"status": "error", "message": f"Model load failed: {model_load_error}"})
                    elif model is None:
                        send_msg(conn, {"status": "loading", "message": "Model is still loading in background"})
                    else:
                        send_msg(conn, {"status": "ok", "message": "Worker is fully ready"})
                
                elif cmd == "shutdown":
                    logger.info("[Worker] Received shutdown signal. Gracefully exiting...")
                    send_msg(conn, {"status": "ok", "message": "Shutting down"})
                    conn.close()
                    
                    try:
                        if model:
                            del model
                        import gc
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception as e:
                        logger.error(f"[Worker] Cleanup error: {e}")
                    
                    import os
                    os._exit(0)
                
                elif cmd == "embed":
                    if model_load_error:
                        send_msg(conn, {"status": "error", "message": f"Model load failed: {model_load_error}"})
                    elif model is None:
                        # 라우터는 이 응답을 받으면 잠시 후 재시도하게 됨
                        send_msg(conn, {"status": "loading", "message": "Model is not ready yet"})
                    else:
                        texts = request.get("texts", [])
                        if not texts:
                            send_msg(conn, {"status": "ok", "embeddings": []})
                        else:
                            embeddings = model.encode(
                                texts,
                                batch_size=16,
                                normalize_embeddings=True,
                                show_progress_bar=False,
                            ).tolist()
                            send_msg(conn, {"status": "ok", "embeddings": embeddings})
                else:
                    send_msg(conn, {"status": "error", "message": f"Unknown command: {cmd}"})
            except Exception as e:
                try:
                    send_msg(conn, {"status": "error", "message": str(e)})
                except Exception:
                    pass
            finally:
                conn.close()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


# ==========================================
# 2. 라우터(Router) 모드 (포트 62384 상주 및 워커 생사 관리)
# ==========================================
worker_process = None
worker_lock = threading.Lock()
worker_request_lock = threading.Lock() # 워커에 대한 단일 요청 직렬화 보장
last_activity_time = time.time()

def ensure_worker_running():
    global worker_process
    with worker_lock:
        if worker_process is not None:
            if worker_process.poll() is not None:
                logger.warning("[Router] Worker process was found dead. Restarting...")
                worker_process = None
        
        if worker_process is None:
            logger.info("[Router] Starting PyTorch Worker Process...")
            env = os.environ.copy()
            env["CORTEX_NO_FILE_LOG"] = "1"
            script_path = os.path.abspath(__file__)

            # 자식 프로세스 기동 (stdout/stderr → 로거 릴레이)
            worker_process = subprocess.Popen(
                [sys.executable, script_path, "--worker"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )

            # 로그 타임스탬프 및 레벨 패턴 (릴레이 시 중복 제거용)
            # 로그 타임스탬프 및 레벨 패턴 (릴레이 시 중복 제거용)
            WORKER_CLEAN_PATTERN = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[.*?\] \[.*?\]\s*")

            def _relay_worker_output(proc):
                try:
                    for line in iter(proc.stdout.readline, b""):
                        text = line.decode("utf-8", errors="replace").rstrip()
                        for msg in text.split('\r'):
                            msg = msg.strip()
                            if msg:
                                # 중복 타임스탬프 제거
                                clean_msg = WORKER_CLEAN_PATTERN.sub("", msg)
                                logger.info(f"[Worker-out] {clean_msg}")
                except Exception:
                    pass
            threading.Thread(target=_relay_worker_output, args=(worker_process,), daemon=True).start()

            # 워커 소켓이 열릴 때까지 최대 20초 폴링 (Cold Start 대기)
            start_time = time.time()
            worker_up = False
            while time.time() - start_time < 20.0:
                # 프로세스가 먼저 종료됐으면 즉시 실패 처리
                exit_code = worker_process.poll()
                if exit_code is not None:
                    logger.error(f"[Router] Worker process exited prematurely (code={exit_code}).")
                    worker_up = False
                    break
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1.0)
                    s.connect((WORKER_HOST, WORKER_PORT))
                    s.close()
                    worker_up = True
                    break
                except (ConnectionRefusedError, socket.timeout, OSError):
                    time.sleep(0.5)

            if not worker_up:
                logger.error("[Router] Worker failed to start within timeout.")
                try:
                    worker_process.kill()
                except Exception:
                    pass
                worker_process = None
                return False
            logger.info("[Router] Worker Process is Ready and listening.")
    return True

def shutdown_worker():
    global worker_process
    with worker_lock:
        if worker_process is not None and worker_process.poll() is None:
            logger.info(f"[Router] IDLE Timeout ({IDLE_TIMEOUT}s) reached. Sending shutdown to worker...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((WORKER_HOST, WORKER_PORT))
                send_msg(s, {"command": "shutdown"})
                s.close()
                # 워커 스스로 종료될 때까지 최대 5초 대기
                worker_process.wait(timeout=5.0)
            except Exception:
                pass
            finally:
                if worker_process.poll() is None:
                    logger.warning("[Router] Worker did not exit gracefully. Force killing...")
                    worker_process.kill()
                worker_process = None
                logger.info("[Router] VRAM fully released (Worker terminated). Standing by.")

def idle_monitor():
    global last_activity_time
    while True:
        time.sleep(10)
        # 워커가 떠있을 때만 타임아웃 검사
        with worker_lock:
            is_running = worker_process is not None and worker_process.poll() is None
        if is_running:
            # [Strict Fix] 현재 요청 처리 중(Lock 획득 상태)이면 유휴 종료를 수행하지 않음
            if worker_request_lock.locked():
                last_activity_time = time.time() # 요청 중이면 타이머 갱신
                continue

            if time.time() - last_activity_time > get_idle_timeout():
                shutdown_worker()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

class RouterHandler(socketserver.BaseRequestHandler):
    def handle(self):
        global last_activity_time
        
        request = recv_msg(self.request)
        if not request:
            return
            
        cmd = request.get("command", "embed")
        
        # [Strict Fix] 핑 요청을 라우터가 직접 응답하지 않고 워커까지 확인하도록 수정
        # (라우터만 떠 있고 워커가 로딩 중일 때 'Ready'로 오판하는 문제 방지)
        if cmd == "ping":
            # 워커가 실행 중인지 확인 (미실행 시 기동 시도)
            try:
                worker_ready = ensure_worker_running()
            except Exception as e:
                logger.error(f"[Router] ensure_worker_running() raised exception: {e}")
                send_msg(self.request, {"status": "error", "message": f"Worker startup exception: {e}"})
                return
            if not worker_ready:
                send_msg(self.request, {"status": "error", "message": "Worker process not started"})
                return

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect((WORKER_HOST, WORKER_PORT))
                send_msg(s, {"command": "ping"})
                response = recv_msg(s)
                s.close()
                if response and response.get("status") == "ok":
                    send_msg(self.request, {"status": "ok", "message": "Cortex Engine (Router+Worker) is fully Ready"})
                else:
                    send_msg(self.request, {"status": "error", "message": "Worker is up but not responding to ping"})
            except Exception as e:
                send_msg(self.request, {"status": "error", "message": f"Worker communication failed: {e}"})
            return
            
        # 실제 작업 요청일 때만 타이머 리셋
        last_activity_time = time.time()

        # [Strict Fix] 워커에 대한 요청을 직렬화하여 동시 접근으로 인한 10054 및 크래시 방지
        with worker_request_lock:
            # 임베딩 등 실제 요청은 워커로 포워딩
            # 1회 재시도 (총 2회) 허용 로직
            for attempt in range(2):
                if not ensure_worker_running():
                    send_msg(self.request, {"status": "error", "message": "Failed to start PyTorch worker process."})
                    return
                
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(15.0) # 클라이언트 타임아웃(10초)과 균형을 맞춘 대기 시간
                    s.connect((WORKER_HOST, WORKER_PORT))
                    send_msg(s, request)
                    response = recv_msg(s)
                    s.close()
                    
                    if response:
                        send_msg(self.request, response)
                        return # 정상 완료 시 루프 종료
                    else:
                        raise Exception("Empty response from worker (connection dropped)")
                        
                except Exception as e:
                    logger.warning(f"[Router] Forwarding to worker failed: {e}. Attempt {attempt+1}/2.")
                    
                    # 워커가 크래시 났다고 간주하고 프로세스 정리
                    global worker_process
                    with worker_lock:
                        if worker_process is not None:
                            if worker_process.poll() is None:
                                worker_process.kill()
                            worker_process = None
                    
                    # 1회 재시도마저 실패한 경우, 즉시 에러 반환
                    if attempt == 1:
                        logger.error("[Router] Worker retry failed. Returning error to client -> CPU Fallback triggered.")
                        send_msg(self.request, {"status": "error", "message": f"Worker crashed repeatedly: {str(e)}"})
                        return

# 로그 정제용 정규표현식: [YYYY-MM-DD HH:MM:SS] [module] [LEVEL] 형태를 감지
LOG_CLEAN_PATTERN = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[.*?\] \[.*?\]\s*")

def _relay_subprocess_output(proc, tag):
    """서브프로세스의 stdout/stderr를 부모 로거로 전달 (중복 타임스탬프 제거) 및 실시간 스트리밍"""
    try:
        for line in iter(proc.stdout.readline, b""):
            # [Inception Guard] 이미 타임스탬프가 있는 경우 제거하여 중복 방지
            decoded_line = line.decode("utf-8", errors="replace").strip()
            if decoded_line:
                clean_line = LOG_CLEAN_PATTERN.sub('', decoded_line)
                logger.info(f"[{tag}] {clean_line}")
    except Exception:
        pass

def main():
    # 1. Watcher 기동
    logger.info("Starting Watcher Daemon from Router...")
    try:
        # 자식 프로세스는 파일 로깅을 금지하고 stdout만 사용 (서버가 통합 관리)
        child_env = os.environ.copy()
        child_env["CORTEX_NO_FILE_LOG"] = "1"
        child_env["PYTHONUNBUFFERED"] = "1"
        
        watcher_proc = subprocess.Popen(
            [sys.executable, "-u", str(WATCHER_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
            start_new_session=True
        )
        threading.Thread(target=_relay_subprocess_output, args=(watcher_proc, "watcher"), daemon=True).start()
    except Exception as e:
        logger.error(f"Failed to launch Watcher: {e}")

    # 2. Worker 가동 및 서버 실행
    run_router()

def run_router():
    # IDLE 감시 스레드 시작
    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()
    
    server = ThreadedTCPServer((ROUTER_HOST, ROUTER_PORT), RouterHandler)
    logger.info(f"[Router] Listening on {ROUTER_HOST}:{ROUTER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[Router] Shutting down...")
        shutdown_worker()
    finally:
        server.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="Run as PyTorch Worker process")
    args, unknown = parser.parse_known_args() # router 인자들과 섞이지 않도록
    
    if args.worker:
        run_worker()
    else:
        main()
