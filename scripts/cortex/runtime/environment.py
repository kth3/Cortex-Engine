"""Environment helpers for Cortex runtime entrypoints and child processes."""
from __future__ import annotations

import os
import sys


def require_virtualenv() -> None:
    """Ensure Cortex control commands run inside the project virtual environment."""
    in_venv = hasattr(sys, "real_prefix") or (sys.base_prefix != sys.prefix)
    if in_venv:
        return

    print("\n[ERROR] Cortex must be run within the virtual environment.")
    print("💡 Hint: Use 'uv run cortex-ctl' or activate .venv first.\n")
    sys.exit(1)


def build_child_env(*, file_log: bool = False) -> dict[str, str]:
    """Build a subprocess environment for Cortex child processes."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if file_log:
        env.pop("CORTEX_NO_FILE_LOG", None)
    else:
        env["CORTEX_NO_FILE_LOG"] = "1"
    return env
