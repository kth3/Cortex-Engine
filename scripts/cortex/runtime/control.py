"""High-level Cortex service control operations."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from cortex.logger import get_logger

from .environment import build_child_env
from .ipc import send_minimal_ping, send_minimal_ping_status
from .local_daemon import resolve_local_daemon_script
from .lock import control_lock
from .paths import CORTEX_HOME, ENGINE_HOST, ENGINE_PORT, LOG_DIR, SERVER_SCRIPT, WATCHER_SCRIPT
from .process import cleanup_ports, force_cleanup_ports, get_pids, launch_background_process, terminate_pid

logger = get_logger("ctl")


def _service_scripts() -> list[tuple[Path, str]]:
    scripts = [(SERVER_SCRIPT, "Engine Server"), (WATCHER_SCRIPT, "Watcher")]
    local_daemon_script = resolve_local_daemon_script(CORTEX_HOME)
    if local_daemon_script:
        scripts.append((local_daemon_script, "Local Daemon"))
    return scripts


def _perform_stop() -> None:
    """Stop services and clean stale runtime state."""
    logger.info("Stopping all Cortex services...")

    all_pids: list[int] = []
    for script, label in _service_scripts():
        pids = get_pids(str(script))
        if pids:
            for pid in pids:
                logger.info(f"Terminating {label} (PID: {pid})...")
                try:
                    os.kill(pid, signal.SIGTERM)
                    all_pids.append(pid)
                except Exception:
                    pass
        else:
            logger.info(f"{label} is not running.")

    if all_pids:
        for pid in all_pids:
            terminate_pid(pid, logger)

        time.sleep(2)
        cleanup_ports(logger, os.getpid())

    force_cleanup_ports(logger, os.getpid())

    logger.info(f"IPC Endpoint: {ENGINE_HOST}:{ENGINE_PORT} (TCP — no file cleanup needed)")

    for log_name in ("watcher_output.log", "engine_server.log"):
        target = LOG_DIR / log_name
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
            logger.info(f"Infrastructure Cleaned: Removed {log_name}")

    logger.info("All services stop/cleanup sequence complete.")


def stop() -> None:
    with control_lock() as acquired:
        if not acquired:
            logger.info("Another control process is running. Skipping stop.")
            return
        _perform_stop()


def _is_local_daemon_running(local_daemon_script: Path | None) -> bool:
    if not local_daemon_script:
        return True
    return bool(get_pids(str(local_daemon_script)))


def _launch_local_daemon(local_daemon_script: Path | None, env: dict[str, str]) -> None:
    if not local_daemon_script:
        return

    logger.info(f"Launching Local Daemon: {local_daemon_script}")
    daemon_proc = launch_background_process(local_daemon_script, env)
    time.sleep(1)
    if daemon_proc.poll() is not None:
        logger.error(
            f"Local Daemon exited immediately (code={daemon_proc.returncode}). "
            "Check local daemon logs or configuration."
        )
    else:
        logger.info("Local Daemon started successfully.")


def start() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with control_lock() as acquired:
        if not acquired:
            logger.info("Another control process is running. Skipping start.")
            return

        current_watchers = get_pids(str(WATCHER_SCRIPT))
        current_servers = get_pids(str(SERVER_SCRIPT))
        local_daemon_script = resolve_local_daemon_script(CORTEX_HOME)

        all_running = (
            bool(current_watchers)
            and bool(current_servers)
            and send_minimal_ping()
            and _is_local_daemon_running(local_daemon_script)
        )
        if all_running:
            return

        _perform_stop()

        logger.info("Starting Unified Cortex Services...")

        sub_env = build_child_env(file_log=True)

        logger.info("Launching GPU Engine Server...")
        server_proc = launch_background_process(SERVER_SCRIPT, sub_env)

        time.sleep(5)
        if server_proc.poll() is not None:
            logger.error(
                f"CRITICAL: Engine Server exited immediately (code={server_proc.returncode}). "
                "Port conflict or startup error."
            )
            return

        logger.info("Waiting for Engine Server to initialize GPU...")
        max_retries = 35
        ready = False
        for retry in range(max_retries):
            if server_proc.poll() is not None:
                logger.error(
                    f"CRITICAL: Engine Server crashed during startup (code={server_proc.returncode})."
                )
                return

            if send_minimal_ping():
                ready = True
                break

            if retry > 0 and retry % 5 == 0:
                logger.warning(f"Engine Server not ready yet (retry {retry}/{max_retries})...")
            time.sleep(1)

        if not ready:
            logger.error("CRITICAL: Engine Server failed to start. Check cortex.log.")
            return

        _launch_local_daemon(local_daemon_script, sub_env)

        logger.info("Engine Server is Ready (GPU Shared Mode).")
        logger.info("Cortex services started successfully.")


def status() -> None:
    server_pids = get_pids(str(SERVER_SCRIPT))
    watcher_pids = get_pids(str(WATCHER_SCRIPT))
    local_daemon_script = resolve_local_daemon_script(CORTEX_HOME)
    ping_status = send_minimal_ping_status()

    label = {"ok": "[READY]", "loading": "[LOADING]", "error": "[ERROR]"}.get(
        ping_status, "[UNREACHABLE]"
    )

    print("\n--- Cortex Status Report (Resident Mode) ---")
    print(f"Engine Server : {'RUNNING' if server_pids else 'STOPPED'} (PIDs: {server_pids}) {label}")
    print(f"Watcher Daemon: {'RUNNING' if watcher_pids else 'STOPPED'} (PIDs: {watcher_pids})")

    if local_daemon_script:
        local_pids = get_pids(str(local_daemon_script))
        print(
            f"Local Daemon  : {'RUNNING' if local_pids else 'STOPPED'} "
            f"(PIDs: {local_pids}) [{local_daemon_script.name}]"
        )

    ipc_ok = ping_status in ("ok", "loading")
    print(f"IPC Endpoint  : {'[OK]' if ipc_ok else '[UNREACHABLE]'} {ENGINE_HOST}:{ENGINE_PORT} (TCP)")
    print(f"Log Path      : {LOG_DIR}/cortex.log")
    print("--------------------------------------------\n")


def restart() -> None:
    """Restart all Cortex services."""
    stop()
    start()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: python cortex_ctl.py [start|stop|restart|status]")
        return 1

    command = args[0].lower()
    if command in {"-h", "--help", "help"}:
        print("Usage: python cortex_ctl.py [start|stop|restart|status]")
        return 0
    if command == "start":
        start()
        return 0
    if command == "stop":
        stop()
        return 0
    if command == "restart":
        restart()
        return 0
    if command == "status":
        status()
        return 0

    print(f"Unknown command: {command}")
    return 1
