"""Environment validation helpers for Cortex runtime entrypoints."""
from __future__ import annotations

import sys


def require_virtualenv() -> None:
    """Ensure Cortex control commands run inside the project virtual environment."""
    in_venv = hasattr(sys, "real_prefix") or (sys.base_prefix != sys.prefix)
    if in_venv:
        return

    print("\n[ERROR] Cortex must be run within the virtual environment.")
    print("💡 Hint: Use 'uv run python scripts/cortex/cortex_ctl.py' or activate .venv first.\n")
    sys.exit(1)
