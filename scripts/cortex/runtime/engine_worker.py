"""PyTorch embedding worker runtime for the Cortex engine server."""
from __future__ import annotations

import gc
import os
import socket
import threading
import traceback
from typing import Any

from cortex.logger import get_logger

from .ipc import recv_msg, send_msg
from .paths import WORKER_PORT

logger = get_logger("server")

WORKER_HOST = "127.0.0.1"


class WorkerState:
    def __init__(self) -> None:
        self.model: Any | None = None
        self.model_load_error: str | None = None
        self.current_device = "cpu"

    @property
    def ready(self) -> bool:
        return self.model is not None and self.model_load_error is None

    def status_response(self) -> dict[str, str]:
        if self.model_load_error:
            return {"status": "error", "message": f"Model load failed: {self.model_load_error}"}
        if self.model is None:
            return {"status": "loading", "message": "Model is still loading in background"}
        return {"status": "ok", "message": "Worker is fully ready"}


def _load_model_bg(state: WorkerState) -> None:
    try:
        # torch import + CUDA 감지를 배경 스레드에서 실행한다.
        import torch

        if torch.cuda.is_available():
            state.current_device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            state.current_device = "mps"
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            state.current_device = "xpu"

        logger.info(f"[Worker] Background model loading started on {state.current_device}...")
        from vector_engine import _load_model

        state.model = _load_model(device=state.current_device)
        logger.info(f"[Worker] Model loading complete. Engine Ready on {state.current_device}.")
    except Exception as exc:
        state.model_load_error = str(exc)
        logger.error(f"[Worker] Background loading failed: {exc}\n{traceback.format_exc()}")


def _shutdown_worker(state: WorkerState) -> None:
    try:
        if state.model:
            del state.model
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
    except Exception as exc:
        logger.error(f"[Worker] Cleanup error: {exc}")

    os._exit(0)


def _handle_embed(conn: socket.socket, request: dict[str, Any], state: WorkerState) -> None:
    if state.model_load_error:
        send_msg(conn, {"status": "error", "message": f"Model load failed: {state.model_load_error}"})
        return

    if state.model is None:
        send_msg(conn, {"status": "loading", "message": "Model is not ready yet"})
        return

    texts = request.get("texts", [])
    if not texts:
        send_msg(conn, {"status": "ok", "embeddings": []})
        return

    embeddings = state.model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()
    send_msg(conn, {"status": "ok", "embeddings": embeddings})


def _handle_worker_request(conn: socket.socket, state: WorkerState) -> bool:
    request = recv_msg(conn)
    if not request:
        return True

    cmd = request.get("command", "embed")

    if cmd == "ping":
        send_msg(conn, state.status_response())
        return True

    if cmd == "shutdown":
        logger.info("[Worker] Received shutdown signal. Gracefully exiting...")
        send_msg(conn, {"status": "ok", "message": "Shutting down"})
        conn.close()
        _shutdown_worker(state)
        return False

    if cmd == "embed":
        _handle_embed(conn, request, state)
        return True

    send_msg(conn, {"status": "error", "message": f"Unknown command: {cmd}"})
    return True


def run_worker() -> None:
    state = WorkerState()

    # 소켓 바인딩을 torch import보다 먼저 실행하여 라우터가 즉시 연결 확인 가능하게 한다.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((WORKER_HOST, WORKER_PORT))
    server.listen(5)
    logger.info(f"[Worker] Server listening on {WORKER_PORT}. Initializing model in background...")

    threading.Thread(target=_load_model_bg, args=(state,), daemon=True).start()

    try:
        while True:
            server.settimeout(1.0)
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue

            try:
                if not _handle_worker_request(conn, state):
                    return
            except Exception as exc:
                try:
                    send_msg(conn, {"status": "error", "message": str(exc)})
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
