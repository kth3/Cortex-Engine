"""File locking helpers for Cortex runtime control."""
from __future__ import annotations

from typing import IO

import portalocker

from .paths import LOCK_FILE, LOG_DIR


def acquire_lock() -> IO[str] | None:
    """Acquire the singleton ctl lock, returning the open lock file handle."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        lock_file = open(LOCK_FILE, "w", encoding="utf-8")
        portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
        return lock_file
    except portalocker.LockException:
        return None
    except (IOError, OSError):
        return None


def release_lock(lock_file: IO[str] | None) -> None:
    if not lock_file:
        return
    try:
        portalocker.unlock(lock_file)
        lock_file.close()
    except Exception:
        pass
