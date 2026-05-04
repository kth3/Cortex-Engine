import os
import sys
import json
import socket
import struct
import time
import argparse
import threading
import subprocess
import socketserver
from typing import List

# 프로젝트 루트 및 스크립트 경로 설정 (모듈 인식을 위해 최상단에서 수행)
CORTEX_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(CORTEX_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.logger import get_logger

logger = get_logger("server")

# IPC: TCP 소켓 (Windows 호환)
ROUTER_HOST = "127.0.0.1"
ROUTER_PORT = 62384
WORKER_HOST = "127.0.0.1"
WORKER_PORT = 62385


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
    current_device = "cpu"
    try:
        from vector_engine import _load_model
        import torch

        if torch.cuda.is_available():
            current_device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            current_device = "mps"
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            current_device = "xpu"

        logger.info(f"[Worker] Attempting to load model on device: {current_device}")
        model = _load_model(device=current_device)
        logger.info(f"[Worker] Engine Ready on {current_device}.")
    except Exception as e:
        import traceback
        logger.error(f"[Worker] Critical Error during startup: {e}")
        logger.error(f"[Worker] Traceback:\n{traceback.format_exc()}")
        sys.exit(1)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((WORKER_HOST, WORKER_PORT))
    server.listen(5)

    try:
        while True:
            server.settimeout(1.0) # Graceful shutdown을 위한 짧은 루프
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
                    send_msg(conn, {"status": "ok", "message": "Worker is alive"})
                elif cmd == "shutdown":
                    # 라우터의 종료 시그널 수신
                    logger.info("[Worker] Received shutdown signal. Gracefully exiting...")
                    send_msg(conn, {"status": "ok", "message": "Shutting down"})
                    conn.close()
                    
                    # Windows 환경에서 프로세스 종료 지연 및 VRAM 미반환(Deadlock) 방지
                    try:
                        del model
                        import gc
                        gc.collect()
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception as e:
                        logger.error(f"[Worker] Cleanup error: {e}")
                    
                    # os._exit(0)를 호출하여 Python GC/Atexit 훅을 건너뛰고 즉시 프로세스 종료 (VRAM 즉각 반환)
                    import os
                    os._exit(0)
                elif cmd == "embed":
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
            script_path = os.path.abspath(__file__)

            # 자식 프로세스 기동 (stdout/stderr → 로거 릴레이)
            worker_process = subprocess.Popen(
                [sys.executable, script_path, "--worker"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )

            def _relay_worker_output(proc):
                try:
                    for line in iter(proc.stdout.readline, b""):
                        msg = line.decode("utf-8", errors="replace").rstrip()
                        if msg:
                            logger.info(f"[Worker-out] {msg}")
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
            if not ensure_worker_running():
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

def run_router():
    # IDLE 감시 스레드 시작
    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()
    
    server = ThreadedTCPServer((ROUTER_HOST, ROUTER_PORT), RouterHandler)
    sys.stderr.write(f"[cortex-server] [ROUTER] Listening on {ROUTER_HOST}:{ROUTER_PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[cortex-server] [ROUTER] Shutting down...\n")
        shutdown_worker()
    finally:
        server.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="Run as PyTorch Worker process")
    args = parser.parse_args()
    
    if args.worker:
        run_worker()
    else:
        run_router()
