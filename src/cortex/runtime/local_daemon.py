"""Local daemon discovery helpers for Cortex runtime control."""
from __future__ import annotations

import os
from pathlib import Path


def resolve_local_daemon_script(cortex_home: Path) -> Path | None:
    """Resolve optional local daemon script from the Cortex .env file."""
    env_path = cortex_home / ".env"
    if not env_path.exists():
        return None

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line.startswith("CORTEX_LOCAL_DAEMON="):
                    continue

                value = line.split("=", 1)[1].strip("'\" ")
                if os.path.exists(value):
                    return Path(value)
                return None
    except Exception:
        return None

    return None
