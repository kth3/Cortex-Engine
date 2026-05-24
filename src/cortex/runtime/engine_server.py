"""Top-level orchestration for the Cortex embedding engine server.

- Server의 책임: Router, WorkerManager, Watcher Daemon, Idle Monitor 등 모든 서버 사이드 컴포넌트를 하나로 묶어 실행하는 진입점 역할을 한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .engine_router import run_router
from .idle_monitor import start_idle_monitor
from .worker_manager import WorkerManager
from .engine_worker import run_worker


def run_engine_server(worker_entrypoint: Path) -> None:
    worker_manager = WorkerManager(worker_entrypoint)
    start_idle_monitor(worker_manager)
    run_router(worker_manager)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="Run as PyTorch Worker process")
    args, _ = parser.parse_known_args(argv)

    if args.worker:
        run_worker()
    else:
        run_engine_server(Path(__file__).resolve())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
