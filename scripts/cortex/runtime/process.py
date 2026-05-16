"""Process management helpers for Cortex runtime control."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil

from .paths import TARGET_PORTS
from .ports import force_release_ports, wait_for_ports_release


def uv_cmd(script: Path) -> list[str]:
    """Use the current interpreter to avoid nested uv wrapping."""
    return [sys.executable, "-u", str(script)]


def get_pids(script_name: str) -> list[int]:
    """Find processes by exact absolute-path token matching."""
    target = os.path.normcase(os.path.abspath(script_name))
    result: list[int] = []

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            for token in (proc.info["cmdline"] or []):
                try:
                    if os.path.normcase(os.path.abspath(token)) == target:
                        result.append(proc.info["pid"])
                        break
                except (ValueError, OSError):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return result


def terminate_pid(pid: int, logger) -> None:
    try:
        psutil.Process(pid).wait(timeout=5)
        logger.info(f"PID {pid} terminated.")
    except psutil.NoSuchProcess:
        logger.info(f"PID {pid} already gone.")
    except psutil.TimeoutExpired:
        logger.warning(f"PID {pid} did not terminate in time. Force killing...")
        try:
            psutil.Process(pid).kill()
            psutil.Process(pid).wait(timeout=3)
            logger.info(f"PID {pid} force killed.")
        except psutil.NoSuchProcess:
            logger.info(f"PID {pid} already gone after kill.")
        except psutil.TimeoutExpired:
            logger.error(f"PID {pid} could not be killed. Port may still be occupied.")


def cleanup_ports(logger, current_pid: int) -> None:
    wait_for_ports_release(logger, TARGET_PORTS, current_pid)


def force_cleanup_ports(logger, current_pid: int) -> None:
    force_release_ports(logger, TARGET_PORTS, current_pid)


def _isolation_kwargs(*, isolate: bool) -> dict:
    """OS-correct flags for spawning a child in its own session/process group.

    POSIX: start_new_session=True → setsid(), independent process group.
    Windows: CREATE_NEW_PROCESS_GROUP enables CTRL_BREAK_EVENT delivery without
    propagating to the parent console. CREATE_NO_WINDOW avoids spawning a
    visible console for background processes.
    """
    if not isolate:
        return {}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def launch_background_process(script: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        uv_cmd(script),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        **_isolation_kwargs(isolate=True),
    )


def launch_logged_process(
    command: list[str],
    env: dict[str, str],
    *,
    start_new_session: bool = False,
) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        **_isolation_kwargs(isolate=start_new_session),
    )
