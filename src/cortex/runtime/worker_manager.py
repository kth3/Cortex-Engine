"""Worker process lifecycle management for the Cortex engine router.

- Manager의 책임: 실제 모델을 보유한 PyTorch Worker 프로세스의 생성(spawn), 감시, 종료(shutdown) 등 생명주기를 전담한다.
- 요청 시 워커가 죽어있으면 다시 살리고, 프로세스 락을 통해 요청을 직렬화하여 전달한다.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from subprocess import Popen
from typing import Any

from cortex.logger import get_logger

from .ipc import send_request
from .paths import WORKER_PORT
from .worker_manager_support import kill_process, spawn_worker_process, start_output_relay, wait_until_listening

logger = get_logger("server")

WORKER_HOST = "127.0.0.1"


class WorkerManager:
    """Owns the embedding worker subprocess and request serialization lock."""

    def __init__(self, worker_entrypoint: Path) -> None:
        self.worker_entrypoint = worker_entrypoint
        self.process: Popen | None = None
        self.lifecycle_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.last_activity_time = time.time()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def touch(self) -> None:
        self.last_activity_time = time.time()

    def start_async(self) -> None:
        threading.Thread(target=self.ensure_running, daemon=True).start()

    def ensure_running(self) -> bool:
        with self.lifecycle_lock:
            if self.process is not None and self.process.poll() is not None:
                logger.warning("[Router] Worker process was found dead. Restarting...")
                self.process = None

            if self.process is None:
                logger.info("[Router] Starting PyTorch Worker Process...")
                self.process = spawn_worker_process(self.worker_entrypoint)
                start_output_relay(self.process, logger)

                if not self._wait_until_listening():
                    logger.error("[Router] Worker failed to start within timeout.")
                    self.kill()
                    return False

                logger.info("[Router] Worker Process is Ready and listening.")

        return True

    def _wait_until_listening(self, timeout: float = 30.0) -> bool:
        if self.process is None:
            return False
        return wait_until_listening(WORKER_HOST, WORKER_PORT, self.process, logger, timeout)

    def ping(self) -> dict[str, Any] | None:
        return send_request(
            {"command": "ping"},
            host=WORKER_HOST,
            port=WORKER_PORT,
            timeout=1.5,
        )

    def forward(self, request: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any] | None:
        return send_request(
            request,
            host=WORKER_HOST,
            port=WORKER_PORT,
            timeout=timeout,
        )

    def kill(self) -> None:
        if self.process is None:
            return
        kill_process(self.process)
        self.process = None

    def shutdown(self, *, reason: str = "shutdown") -> None:
        with self.lifecycle_lock:
            if not self.is_alive():
                self.process = None
                return

            logger.info(f"[Router] {reason}. Sending shutdown to worker...")
            try:
                send_request(
                    {"command": "shutdown"},
                    host=WORKER_HOST,
                    port=WORKER_PORT,
                    timeout=3.0,
                )
                if self.process is not None:
                    self.process.wait(timeout=5.0)
            except Exception:
                pass
            finally:
                if self.process is not None and self.process.poll() is None:
                    logger.warning("[Router] Worker did not exit gracefully. Force killing...")
                    self.process.kill()
                self.process = None
                logger.info("[Router] VRAM fully released (Worker terminated). Standing by.")

    def forward_with_retry(self, request: dict[str, Any], *, attempts: int = 2) -> dict[str, Any]:
        self.touch()

        with self.request_lock:
            for attempt in range(attempts):
                if not self.ensure_running():
                    return {"status": "error", "message": "Failed to start PyTorch worker process."}

                try:
                    response = self.forward(request)
                    if response:
                        return response
                    raise RuntimeError("Empty response from worker (connection dropped)")
                except Exception as exc:
                    logger.warning(
                        f"[Router] Forwarding to worker failed: {exc}. Attempt {attempt + 1}/{attempts}."
                    )
                    self.kill()

                    if attempt == attempts - 1:
                        logger.error("[Router] Worker retry failed. Returning error to client -> CPU Fallback triggered.")
                        return {"status": "error", "message": f"Worker crashed repeatedly: {str(exc)}"}

        return {"status": "error", "message": "Worker request failed unexpectedly."}
