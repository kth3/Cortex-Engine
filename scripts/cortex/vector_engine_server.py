import argparse
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

# 경로 설정 및 모듈 임포트
CORTEX_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = str(CORTEX_DIR.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.logger import get_logger
from cortex.paths import resolve_workspace
from cortex.runtime.ipc import recv_msg, send_msg, send_request
from cortex.runtime.logging import relay_subprocess_output
from cortex.runtime.paths import ENGINE_HOST as ROUTER_HOST, ENGINE_PORT as ROUTER_PORT, WATCHER_SCRIPT, WORKER_PORT

# 서버는 직접 파일에 로그를 남겨야 함 (ctl이 종료된 후에도 유지되도록)
logger = get_logger("server")

WORKER_HOST = "127.0.0.1"
WORKSPACE = resolve_workspace(CORTEX_DIR)


def get_idle_timeout() -> int:
    try:
        from cortex.indexer_utils import load_settings

        settings = load_settings(str(WORKSPACE))
        rules = settings.get("indexing_rules", {})
        timeout = rules.get("idle_timeout") or settings.get("idle_timeout")
        if timeout is not None:
            return int(timeout)
    except Exception:
        pass
    return 300


IDLE_TIMEOUT = get_idle_timeout()


# ==========================================
# 1. 워커(Worker) 모드 (PyTorch 및 모델 로드 전담)
# ==========================================
def run_worker():
    model = None
    model_load_error = None
    current_device = "cpu"

    def _load_model_bg():
        nonlocal model, model_load_error, current_device
        try:
            # [Root Cause Fix] torch import + CUDA 감지를 배경 스레드에서 실행
            import torch

            if torch.cuda.is_available():
                current_device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                current_device = "mps"
            elif hasattr(torch, "xpu") and torch.xpu.is_available():
                current_device = "xpu"
            logger.info(f"[Worker] Background model loading started on {current_device}...")
            from vector_engine import _load_model

            model = _load_model(device=current_device)
            logger.info(f"[Worker] Model loading complete. Engine Ready on {current_device}.")
        except Exception as e:
            import traceback

            model_load_error = str(e)
            logger.error(f"[Worker] Background loading failed: {e}\n{traceback.format_exc()}")

    # 소켓 바인딩을 torch import보다 먼저 실행 → 라우터가 즉시 연결 확인 가능
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((WORKER_HOST, WORKER_PORT))
    server.listen(5)
    logger.info(f"[Worker] Server listening on {WORKER_PORT}. Initializing model in background...")

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
                        try:
                            import torch

                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except ImportError:
                            pass
                    except Exception as e:
                        logger.error(f"[Worker] Cleanup error: {e}")

                    os._exit(0)

                elif cmd == "embed":
                    if model_load_error:
                        send_msg(conn, {"status": "error", "message": f"Model load failed: {model_load_error}"})
                    elif model is None:
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
# 2. 라우터(Router) 모드 (포트 42384 상주 및 워커 생사 관리)
# ==========================================
worker_process = None
worker_lock = threading.Lock()
worker_request_lock = threading.Lock()
last_activity_time = time.time()


def ensure_worker_running():
    global worker_process
    with worker_lock:
        if worker_process is not None and worker_process.poll() is not None:
            logger.warning("[Router] Worker process was found dead. Restarting...")
            worker_process = None

        if worker_process is None:
            logger.info("[Router] Starting PyTorch Worker Process...")
            env = os.environ.copy()
            env["CORTEX_NO_FILE_LOG"] = "1"
            script_path = os.path.abspath(__file__)

            worker_process = subprocess.Popen(
                [sys.executable, script_path, "--worker"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            threading.Thread(
                target=relay_subprocess_output,
                args=(worker_process, "Worker-out", logger),
                daemon=True,
            ).start()

            start_time = time.time()
            worker_up = False
            while time.time() - start_time < 30.0:
                exit_code = worker_process.poll()
                if exit_code is not None:
                    logger.error(f"[Router] Worker process exited prematurely (code={exit_code}).")
                    worker_up = False
                    break
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1.0)
                    sock.connect((WORKER_HOST, WORKER_PORT))
                    sock.close()
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
                send_request(
                    {"command": "shutdown"},
                    host=WORKER_HOST,
                    port=WORKER_PORT,
                    timeout=3.0,
                )
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
        with worker_lock:
            is_running = worker_process is not None and worker_process.poll() is None
        if is_running:
            if worker_request_lock.locked():
                last_activity_time = time.time()
                continue

            if time.time() - last_activity_time > get_idle_timeout():
                shutdown_worker()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class RouterHandler(socketserver.BaseRequestHandler):
    def handle(self):
        global last_activity_time, worker_process

        request = recv_msg(self.request)
        if not request:
            return

        cmd = request.get("command", "embed")

        if cmd == "ping":
            with worker_lock:
                worker_alive = worker_process is not None and worker_process.poll() is None
            if not worker_alive:
                threading.Thread(target=ensure_worker_running, daemon=True).start()
                send_msg(self.request, {"status": "loading", "message": "Worker is being started"})
                return

            try:
                response = send_request(
                    {"command": "ping"},
                    host=WORKER_HOST,
                    port=WORKER_PORT,
                    timeout=1.5,
                )
                send_msg(
                    self.request,
                    response or {"status": "error", "message": "Empty response from worker"},
                )
            except Exception as e:
                send_msg(self.request, {"status": "loading", "message": f"Worker not yet listening: {e}"})
            return

        last_activity_time = time.time()

        with worker_request_lock:
            for attempt in range(2):
                if not ensure_worker_running():
                    send_msg(self.request, {"status": "error", "message": "Failed to start PyTorch worker process."})
                    return

                try:
                    response = send_request(
                        request,
                        host=WORKER_HOST,
                        port=WORKER_PORT,
                        timeout=15.0,
                    )

                    if response:
                        send_msg(self.request, response)
                        return
                    raise Exception("Empty response from worker (connection dropped)")

                except Exception as e:
                    logger.warning(f"[Router] Forwarding to worker failed: {e}. Attempt {attempt + 1}/2.")

                    with worker_lock:
                        if worker_process is not None:
                            if worker_process.poll() is None:
                                worker_process.kill()
                            worker_process = None

                    if attempt == 1:
                        logger.error("[Router] Worker retry failed. Returning error to client -> CPU Fallback triggered.")
                        send_msg(
                            self.request,
                            {"status": "error", "message": f"Worker crashed repeatedly: {str(e)}"},
                        )
                        return


def main():
    logger.info("Starting Watcher Daemon from Router...")
    try:
        child_env = os.environ.copy()
        child_env["CORTEX_NO_FILE_LOG"] = "1"
        child_env["PYTHONUNBUFFERED"] = "1"

        watcher_proc = subprocess.Popen(
            [sys.executable, "-u", str(WATCHER_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
            start_new_session=True,
        )
        threading.Thread(
            target=relay_subprocess_output,
            args=(watcher_proc, "watcher", logger),
            daemon=True,
        ).start()
    except Exception as e:
        logger.error(f"Failed to launch Watcher: {e}")

    run_router()


def run_router():
    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()

    bind_deadline = time.time() + 20.0
    server = None
    while time.time() < bind_deadline:
        try:
            server = ThreadedTCPServer((ROUTER_HOST, ROUTER_PORT), RouterHandler)
            break
        except OSError as e:
            remaining = bind_deadline - time.time()
            if remaining <= 0:
                logger.error(f"[Router] Failed to bind {ROUTER_HOST}:{ROUTER_PORT} after 20s: {e}")
                raise
            logger.warning(f"[Router] Port {ROUTER_PORT} not yet released ({e}). Retrying ({remaining:.0f}s left)...")
            time.sleep(0.5)

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
    args, unknown = parser.parse_known_args()

    if args.worker:
        run_worker()
    else:
        main()
