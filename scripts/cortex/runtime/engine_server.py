"""Top-level orchestration for the Cortex embedding engine server."""
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
