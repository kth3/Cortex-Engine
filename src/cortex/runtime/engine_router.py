"""TCP router runtime for the Cortex embedding engine server.

- Router의 책임: 외부 클라이언트(예: CLI 도구, MCP 서버)의 연결을 받아 WorkerManager로 요청을 전달(라우팅)한다.
- 워커 상태에 따라 요청을 대기시키거나, 재시도를 수행하는 통신 앞단 역할을 한다.
"""
from __future__ import annotations

import socketserver
import time

from cortex.logger import get_logger

from .ipc import recv_msg, send_msg
from .paths import ENGINE_HOST as ROUTER_HOST, ENGINE_PORT as ROUTER_PORT
from .worker_manager import WorkerManager

logger = get_logger("server")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class RouterHandler(socketserver.BaseRequestHandler):
    worker_manager: WorkerManager | None = None

    def handle(self) -> None:
        if self.worker_manager is None:
            send_msg(self.request, {"status": "error", "message": "Worker manager is not configured"})
            return

        request = recv_msg(self.request)
        if not request:
            return

        cmd = request.get("command", "embed")

        if cmd == "ping":
            self._handle_ping()
            return

        response = self.worker_manager.forward_with_retry(request)
        send_msg(self.request, response)

    def _handle_ping(self) -> None:
        assert self.worker_manager is not None

        if not self.worker_manager.is_alive():
            self.worker_manager.start_async()
            send_msg(self.request, {"status": "loading", "message": "Worker is being started"})
            return

        try:
            response = self.worker_manager.ping()
            send_msg(
                self.request,
                response or {"status": "error", "message": "Empty response from worker"},
            )
        except Exception as exc:
            send_msg(self.request, {"status": "loading", "message": f"Worker not yet listening: {exc}"})


def run_router(worker_manager: WorkerManager) -> None:
    RouterHandler.worker_manager = worker_manager

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
        worker_manager.shutdown(reason="Router shutdown")
    finally:
        server.server_close()
