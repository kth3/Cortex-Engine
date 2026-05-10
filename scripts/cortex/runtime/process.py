"""Process management helpers for Cortex runtime control."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .paths import ENGINE_PORT, TARGET_PORTS


UV_BIN = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


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
    deadline = time.time() + 8.0

    while time.time() < deadline:
        occupied = []
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port in TARGET_PORTS and conn.status in (
                    "LISTEN",
                    "CLOSE_WAIT",
                    "ESTABLISHED",
                    "TIME_WAIT",
                ):
                    if conn.pid and conn.pid != current_pid:
                        occupied.append((conn.laddr.port, conn.pid, conn.status))
        except Exception:
            pass

        if not occupied:
            break

        logger.warning(f"포트 아직 점유 중: {occupied}. 재확인 대기...")
        time.sleep(1.0)


def force_cleanup_ports(logger, current_pid: int) -> None:
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if (
                conn.laddr.port in TARGET_PORTS
                and conn.pid
                and conn.pid != current_pid
                and conn.status in ("LISTEN", "CLOSE_WAIT", "ESTABLISHED")
            ):
                logger.warning(
                    f"Port {conn.laddr.port} still occupied by PID {conn.pid}. Force killing..."
                )
                try:
                    process = psutil.Process(conn.pid)
                    process.kill()
                    process.wait(timeout=3)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception as exc:
        logger.debug(f"Port cleanup exception (non-critical): {exc}")


def launch_background_process(script: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        uv_cmd(script),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
