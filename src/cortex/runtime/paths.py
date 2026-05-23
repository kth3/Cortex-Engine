"""Runtime path constants for Cortex service control."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cortex.paths import history_dir, resolve_cortex_home, resolve_workspace

CORTEX_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CORTEX_DIR.parent.parent
WORKSPACE = resolve_workspace(CORTEX_DIR)
CORTEX_HOME = resolve_cortex_home(WORKSPACE)
LOG_DIR = history_dir(WORKSPACE)

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 42384
WORKER_PORT = 42385
TARGET_PORTS = [ENGINE_PORT, WORKER_PORT]

SERVER_SCRIPT = CORTEX_DIR / "runtime" / "engine_server.py"
RUST_WATCHER_BINARY_NAME = "cortex-watcher.exe" if os.name == "nt" else "cortex-watcher"


def resolve_rust_watcher_binary() -> Path:
    rust_target_dir = REPO_ROOT / "rust" / "target"
    release_binary = rust_target_dir / "release" / RUST_WATCHER_BINARY_NAME
    debug_binary = rust_target_dir / "debug" / RUST_WATCHER_BINARY_NAME
    for candidate in (release_binary, debug_binary):
        if candidate.exists():
            return candidate
    return release_binary


WATCHER_BINARY = resolve_rust_watcher_binary()
WATCHER_SCRIPT = WATCHER_BINARY
LOCK_FILE = LOG_DIR / "cortex_ctl.lock"


def ensure_rust_watcher_binary() -> Path:
    binary = resolve_rust_watcher_binary()
    if binary.exists():
        return binary

    cargo_manifest = REPO_ROOT / "rust" / "Cargo.toml"
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(cargo_manifest),
            "-p",
            "cortex-watcher",
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    return binary
