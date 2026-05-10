"""Router runtime for the Cortex embedding engine server."""
from __future__ import annotations

import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

from cortex.logger import get_logger
from cortex.paths import resolve_workspace

from .ipc import recv_msg, send_msg, send_request
from .logging import relay_subprocess_output
from .paths import ENGINE_HOST as ROUTER_HOST, ENGINE_PORT as ROUTER_PORT, WATCHER_SCRIPT, WORKER_PORT
from .process import launch_logged_process

logger = get_logger("server")

WORKER_HOST = "127.0.0.1"
CORTEX_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = resolve_workspace(CORTEX_DIR)

worker_process = None
worker_lock = threading.Lock()
worker_request_lock = threading.Lock()
last_activity_time = time.time()


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


def is_worker_alive() -> bool:
    return worker_process is not None and worker_process.poll() is None


def build_child_env(*, file_log: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if file_log:
        env.pop("CORTEX_NO_FILE_LOG", None)
    else:
        env["CORTEX_NO_FILE_LOG"] = "1"
    return env


def ensure_worker_running(worker_entrypoint: Path) -> bool:
    global worker_process
    with worker_lock:
        if worker_process is not None and worker_process.poll() is not None:
            logger.warning("[Router] Worker process was found dead. Restarting...")
            worker_process = None

        if worker_process is None:
            logger.info("[Router] Starting PyTorch Worker Process...")
            worker_process = launch_logged_process(
                [sys.executable, str(worker_entrypoint), "--worker"],
                build_child_env(),
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


def shutdown_worker() -> None:
    global worker_process
    with worker_lock:
        if not is_worker_alive():
            return

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
            if worker_process and worker_process.poll() is None:
                logger.warning("[Router] Worker did not exit gracefully. Force killing...")
                worker_process.kill()
            worker_process = None
            logger.info("[Router] VRAM fully released (Worker terminated). Standing by.")


def idle_monitor() -> None:
    global last_activity_time
    while True:
        time.sleep(10)
        with worker_lock:
            running = is_worker_alive()
        if not running:
            continue

        if worker_request_lock.locked():
            last_activity_time = time.time()
            continue

        if time.time() - last_activity_time > get_idle_timeout():
            shutdown_worker()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class RouterHandler(socketserver.BaseRequestHandler):
    worker_entrypoint: Path | None = None

    def handle(self) -> None:
        global last_activity_time, worker_process

        if self.worker_entrypoint is None:
            send_msg(self.request, {"status": "error", "message": "Worker entrypoint is not configured"})
            return

        request = recv_msg(self.request)
        if not request:
            return

        cmd = request.get("command", "embed")

        if cmd == "ping":
            with worker_lock:
                worker_alive = is_worker_alive()
            if not worker_alive:
                threading.Thread(target=ensure_worker_running, args=(self.worker_entrypoint,), daemon=True).start()
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
            except Exception as exc:
                send_msg(self.request, {"status": "loading", "message": f"Worker not yet listening: {exc}"})
            return

        last_activity_time = time.time()

        with worker_request_lock:
            for attempt in range(2):
                if not ensure_worker_running(self.worker_entrypoint):
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

                except Exception as exc:
                    logger.warning(f"[Router] Forwarding to worker failed: {exc}. Attempt {attempt + 1}/2.")

                    with worker_lock:
                        if worker_process is not None:
                            if worker_process.poll() is None:
                                worker_process.kill()
                            worker_process = None

                    if attempt == 1:
                        logger.error("[Router] Worker retry failed. Returning error to client -> CPU Fallback triggered.")
                        send_msg(
                            self.request,
                            {"status": "error", "message": f"Worker crashed repeatedly: {str(exc)}"},
                        )
                        return


def launch_watcher() -> None:
    logger.info("Starting Watcher Daemon from Router...")
    try:
        watcher_proc = launch_logged_process(
            [sys.executable, "-u", str(WATCHER_SCRIPT)],
            build_child_env(),
            start_new_session=True,
        )
        threading.Thread(
            target=relay_subprocess_output,
            args=(watcher_proc, "watcher", logger),
            daemon=True,
        ).start()
    except Exception as exc:
        logger.error(f"Failed to launch Watcher: {exc}")


def run_router(worker_entrypoint: Path) -> None:
    RouterHandler.worker_entrypoint = worker_entrypoint
    threading.Thread(target=idle_monitor, daemon=True).start()

    bind_deadline = time.time() + 20.0
    server = None
    while time.time() < bind_deadline:
        try:
            server = ThreadedTCPServer((ROUTER_HOST, ROUTER_PORT), RouterHandler)
            break
        except OSError as exc:
            remaining = bind_deadline - time.time()
            if remaining <= 0:
                logger.error(f"[Router] Failed to bind {ROUTER_HOST}:{ROUTER_PORT} after 20s: {exc}")
                raise
            logger.warning(f"[Router] Port {ROUTER_PORT} not yet released ({exc}). Retrying ({remaining:.0f}s left)...")
            time.sleep(0.5)

    if server is None:
        raise RuntimeError(f"Router failed to bind {ROUTER_HOST}:{ROUTER_PORT}")

    logger.info(f"[Router] Listening on {ROUTER_HOST}:{ROUTER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[Router] Shutting down...")
        shutdown_worker()
    finally:
        server.server_close()


def run_engine_server(worker_entrypoint: Path) -> None:
    launch_watcher()
    run_router(worker_entrypoint)
