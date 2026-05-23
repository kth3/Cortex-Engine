"""Compatibility daemon helpers for legacy watch tests."""
from __future__ import annotations

import signal

_SHUTDOWN_REQUESTED = False


def _install_signal_handlers(observer) -> None:
    def _handle_shutdown(signum, frame):  # noqa: ARG001
        global _SHUTDOWN_REQUESTED
        _SHUTDOWN_REQUESTED = True
        observer.stop()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_shutdown)

