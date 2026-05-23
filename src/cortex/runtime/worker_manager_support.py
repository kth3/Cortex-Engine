"""Helper functions for Cortex worker process lifecycle."""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from subprocess import Popen

from .environment import build_child_env
from .logging import relay_subprocess_output
from .process import launch_logged_process


def spawn_worker_process(worker_entrypoint: Path) -> Popen:
    return launch_logged_process(
        [sys.executable, str(worker_entrypoint), "--worker"],
        build_child_env(),
    )


def start_output_relay(process: Popen, logger) -> None:
    import threading

    threading.Thread(
        target=relay_subprocess_output,
        args=(process, "Worker-out", logger),
        daemon=True,
    ).start()


def wait_until_listening(host: str, port: int, process: Popen, logger, timeout: float = 30.0) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        exit_code = process.poll()
        if exit_code is not None:
            logger.error(f"[Router] Worker process exited prematurely (code={exit_code}).")
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect((host, port))
            sock.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.5)

    return False


def kill_process(process: Popen) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
