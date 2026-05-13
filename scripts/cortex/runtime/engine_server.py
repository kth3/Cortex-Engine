"""Top-level orchestration for the Cortex embedding engine server.

- Server의 책임: Router, WorkerManager, Watcher Daemon, Idle Monitor 등 모든 서버 사이드 컴포넌트를 하나로 묶어 실행하는 진입점 역할을 한다.
"""
from __future__ import annotations

from pathlib import Path

from .engine_router import run_router
from .idle_monitor import start_idle_monitor
from .watcher_launcher import launch_watcher
from .worker_manager import WorkerManager


def run_engine_server(worker_entrypoint: Path) -> None:
    worker_manager = WorkerManager(worker_entrypoint)
    launch_watcher()
    start_idle_monitor(worker_manager)
    run_router(worker_manager)
