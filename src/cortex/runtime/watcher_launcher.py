"""Watcher subprocess launcher for the Cortex engine server."""
from __future__ import annotations

import os
import threading
import sys

from cortex.logger import get_logger
from cortex.paths import workspace_key

from .environment import build_child_env
from .logging import relay_subprocess_output
from .paths import REPO_ROOT, WORKSPACE, ensure_rust_watcher_binary
from .process import launch_logged_process

logger = get_logger("server")


def launch_watcher() -> None:
    logger.info("Starting Watcher Daemon from Router...")
    try:
        env = build_child_env()
        env["CORTEX_WORKSPACE_KEY"] = workspace_key(WORKSPACE)
        env["CORTEX_PYTHON_EXECUTABLE"] = sys.executable
        env["CORTEX_PYTHON_FALLBACK"] = sys.executable
        scripts_dir = str(REPO_ROOT / "scripts")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{scripts_dir}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else scripts_dir
        )
        watcher_binary = ensure_rust_watcher_binary()

        watcher_proc = launch_logged_process(
            [str(watcher_binary), "watch", "--workspace", str(WORKSPACE)],
            env,
            start_new_session=True,
        )
        threading.Thread(
            target=relay_subprocess_output,
            args=(watcher_proc, "watcher", logger),
            daemon=True,
        ).start()
    except Exception as exc:
        logger.error(f"Failed to launch Watcher: {exc}")
