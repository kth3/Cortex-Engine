"""Runtime path constants for Cortex service control."""
from __future__ import annotations

from pathlib import Path

from cortex.paths import history_dir, resolve_cortex_home, resolve_workspace

CORTEX_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = resolve_workspace(CORTEX_DIR)
CORTEX_HOME = resolve_cortex_home(WORKSPACE)
LOG_DIR = history_dir(WORKSPACE)

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 42384
WORKER_PORT = 42385
TARGET_PORTS = [ENGINE_PORT, WORKER_PORT]

SERVER_SCRIPT = CORTEX_DIR / "vector_engine_server.py"
WATCHER_SCRIPT = CORTEX_DIR / "watcher.py"
LOCK_FILE = LOG_DIR / "cortex_ctl.lock"
