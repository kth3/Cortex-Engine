"""Idle timeout monitoring for Cortex embedding workers.

- Idle Monitor의 책임: 지정된 시간(idle timeout) 동안 요청이 없을 때 워커 프로세스를 종료시킨다.
- 이 모니터가 manager.shutdown()을 호출하여 워커 프로세스가 종료됨으로써, 장기 미사용 시 GPU model VRAM이 자동으로 해제(lifecycle 연결)되는 효과를 낸다.
"""
from __future__ import annotations

import threading
import time

from cortex.logger import get_logger
from cortex.paths import resolve_workspace

from .paths import CORTEX_DIR
from .worker_manager import WorkerManager

logger = get_logger("server")
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


def run_idle_monitor(manager: WorkerManager, *, interval: float = 10.0) -> None:
    while True:
        time.sleep(interval)
        with manager.lifecycle_lock:
            running = manager.is_alive()
        if not running:
            continue

        if manager.request_lock.locked():
            manager.touch()
            continue

        timeout = get_idle_timeout()
        if time.time() - manager.last_activity_time > timeout:
            manager.shutdown(reason=f"IDLE Timeout ({timeout}s) reached")


def start_idle_monitor(manager: WorkerManager) -> threading.Thread:
    thread = threading.Thread(target=run_idle_monitor, args=(manager,), daemon=True)
    thread.start()
    return thread
