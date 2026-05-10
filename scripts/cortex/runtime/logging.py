"""Logging relay helpers for Cortex runtime control."""
from __future__ import annotations

import re


# 예: [2026-05-04 17:21:15] [cortex.server] [INFO]
LOG_CLEAN_PATTERN = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[[^\]]+\] \[[A-Z]+\]\s*"
)


def relay_subprocess_output(proc, label: str, logger) -> None:
    """Relay subprocess stdout/stderr through the parent logger."""
    try:
        for line in iter(proc.stdout.readline, b""):
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                clean_msg = LOG_CLEAN_PATTERN.sub("", msg)
                logger.info(f"[{label}] {clean_msg}")
    except Exception:
        pass
