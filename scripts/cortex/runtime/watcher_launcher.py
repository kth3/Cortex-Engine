"""Watcher subprocess launcher for the Cortex engine server."""
from __future__ import annotations

import sys
import threading

from cortex.logger import get_logger

from .environment import build_child_env
from .logging import relay_subprocess_output
from .paths import WATCHER_SCRIPT
from .process import launch_logged_process

logger = get_logger("server")


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
