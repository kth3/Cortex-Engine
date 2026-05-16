"""High-level Cortex service control operations."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import psutil

from cortex.logger import get_logger

from .environment import build_child_env
from .ipc import send_minimal_ping, send_minimal_ping_status
from .local_daemon import resolve_local_daemon_script
from .lock import control_lock
from .paths import CORTEX_HOME, ENGINE_HOST, ENGINE_PORT, LOG_DIR, SERVER_SCRIPT, WATCHER_SCRIPT
from .process import cleanup_ports, force_cleanup_ports, get_pids, launch_background_process, terminate_pid


def _request_graceful_stop(pid: int) -> bool:
    """Ask a child process to stop, OS-correctly.

    POSIX: SIGTERM. Child SIGTERM handler can run cleanup.
    Windows: CTRL_BREAK_EVENT to a child started with
    CREATE_NEW_PROCESS_GROUP. Child SIGBREAK handler can run cleanup.
    Falls back to terminate()/TerminateProcess on failure.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            return True
        except (ValueError, PermissionError, psutil.AccessDenied, OSError):
            try:
                proc.terminate()
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False
    try:
        proc.terminate()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

logger = get_logger("ctl")

STOP_PORT_RELEASE_GRACE_SECONDS = 2
SERVER_STARTUP_SETTLE_SECONDS = 5
LOCAL_DAEMON_SETTLE_SECONDS = 1
ENGINE_READY_MAX_RETRIES = 35
ENGINE_READY_POLL_INTERVAL_SECONDS = 1
ENGINE_READY_WARNING_INTERVAL_RETRIES = 5
CLEANUP_LOG_FILENAMES = ("watcher_output.log", "engine_server.log")


def _service_scripts() -> list[tuple[Path, str]]:
    scripts = [(SERVER_SCRIPT, "Engine Server"), (WATCHER_SCRIPT, "Watcher")]
    local_daemon_script = resolve_local_daemon_script(CORTEX_HOME)
    if local_daemon_script:
        scripts.append((local_daemon_script, "Local Daemon"))
    return scripts


def _cleanup_runtime_logs() -> None:
    for log_name in CLEANUP_LOG_FILENAMES:
        target = LOG_DIR / log_name
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
            logger.info(f"Infrastructure Cleaned: Removed {log_name}")


def _perform_stop() -> None:
    """Stop services and clean stale runtime state.
    
    graceful stop 이후 포트 cleanup(port release)까지 수행하는 완전한 정리 경로다.
    """
    logger.info("Stopping all Cortex services...")

    all_pids: list[int] = []
    for script, label in _service_scripts():
        pids = get_pids(str(script))
        if pids:
            for pid in pids:
                logger.info(f"Terminating {label} (PID: {pid})...")
                if _request_graceful_stop(pid):
                    all_pids.append(pid)
        else:
            logger.info(f"{label} is not running.")

    if all_pids:
        for pid in all_pids:
            terminate_pid(pid, logger)

        time.sleep(STOP_PORT_RELEASE_GRACE_SECONDS)
        cleanup_ports(logger, os.getpid())

    force_cleanup_ports(logger, os.getpid())

    logger.info(f"IPC Endpoint: {ENGINE_HOST}:{ENGINE_PORT} (TCP — no file cleanup needed)")

    _cleanup_runtime_logs()

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


def _wait_for_engine_ready(server_proc) -> bool:
    logger.info("Waiting for Engine Server to initialize GPU...")

    for retry in range(ENGINE_READY_MAX_RETRIES):
        if server_proc.poll() is not None:
            logger.error(
                f"CRITICAL: Engine Server crashed during startup (code={server_proc.returncode})."
            )
            return False

        if send_minimal_ping():
            return True

        if retry > 0 and retry % ENGINE_READY_WARNING_INTERVAL_RETRIES == 0:
            logger.warning(
                f"Engine Server not ready yet (retry {retry}/{ENGINE_READY_MAX_RETRIES})..."
            )
        time.sleep(ENGINE_READY_POLL_INTERVAL_SECONDS)

    logger.error("CRITICAL: Engine Server failed to start. Check cortex.log.")
    return False


def _launch_local_daemon(local_daemon_script: Path | None, env: dict[str, str]) -> None:
    if not local_daemon_script:
        return

    logger.info(f"Launching Local Daemon: {local_daemon_script}")
    daemon_proc = launch_background_process(local_daemon_script, env)
    time.sleep(LOCAL_DAEMON_SETTLE_SECONDS)
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

        time.sleep(SERVER_STARTUP_SETTLE_SECONDS)
        if server_proc.poll() is not None:
            logger.error(
                f"CRITICAL: Engine Server exited immediately (code={server_proc.returncode}). "
                "Port conflict or startup error."
            )
            return

        if not _wait_for_engine_ready(server_proc):
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


_USAGE = "Usage: cortex-ctl [start|stop|restart|status|knowledge ...|migrate ...|bootstrap ...]"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_USAGE)
        return 1

    command = args[0].lower()
    if command in {"-h", "--help", "help"}:
        print(_USAGE)
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
    if command == "knowledge":
        from cortex.runtime import knowledge_cli
        return knowledge_cli.main(args[1:])
    if command == "migrate":
        from cortex.runtime import migrate_cli
        return migrate_cli.main(args[1:])
    if command == "bootstrap":
        from cortex.runtime import bootstrap_cli
        return bootstrap_cli.main(args[1:])

    print(f"Unknown command: {command}")
    return 1
