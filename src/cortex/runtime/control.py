"""High-level Cortex service control operations."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

from cortex.logger import get_logger

from .environment import build_child_env
from .ipc import send_minimal_ping, send_minimal_ping_status
from .local_daemon import resolve_local_daemon_script
from .lock import control_lock
from .control_support import (
    cleanup_runtime_logs as _cleanup_runtime_logs_impl,
    is_local_daemon_running as _is_local_daemon_running_impl,
    launch_local_daemon as _launch_local_daemon_impl,
    perform_stop as _perform_stop_impl,
    resolve_start_timeout as _resolve_start_timeout_impl,
    service_scripts as _service_scripts_impl,
    wait_for_engine_ready as _wait_for_engine_ready_impl,
)
from .paths import (
    CORTEX_HOME,
    ENGINE_HOST,
    ENGINE_PORT,
    LOG_DIR,
    SERVER_SCRIPT,
    resolve_rust_watcher_binary,
)
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
DEFAULT_ENGINE_READY_MAX_RETRIES = 35
ENGINE_READY_POLL_INTERVAL_SECONDS = 1
ENGINE_READY_WARNING_INTERVAL_RETRIES = 5
CLEANUP_LOG_FILENAMES = ("watcher_output.log", "engine_server.log")


def _watcher_binary() -> Path:
    return resolve_rust_watcher_binary()


def _resolve_start_timeout() -> int:
    return _resolve_start_timeout_impl()


def _service_scripts() -> list[tuple[Path, str]]:
    return _service_scripts_impl(CORTEX_HOME, resolve_local_daemon_script, _watcher_binary())


def _cleanup_runtime_logs() -> None:
    _cleanup_runtime_logs_impl(LOG_DIR, logger)


def _perform_stop() -> None:
    """Stop services and clean stale runtime state."""
    _perform_stop_impl(
        logger=logger,
        service_scripts=_service_scripts(),
        get_pids=get_pids,
        request_graceful_stop=_request_graceful_stop,
        terminate_pid=terminate_pid,
        cleanup_ports=cleanup_ports,
        force_cleanup_ports=force_cleanup_ports,
        os_getpid=os.getpid,
        sleep_fn=time.sleep,
        stop_port_release_grace_seconds=STOP_PORT_RELEASE_GRACE_SECONDS,
    )


def stop() -> None:
    with control_lock() as acquired:
        if not acquired:
            logger.info("Another control process is running. Skipping stop.")
            return
        _perform_stop()


def _is_local_daemon_running(local_daemon_script: Path | None) -> bool:
    return _is_local_daemon_running_impl(local_daemon_script, get_pids)


def _wait_for_engine_ready(server_proc) -> bool:
    return _wait_for_engine_ready_impl(
        server_proc,
        logger,
        _resolve_start_timeout,
        send_minimal_ping_status,
        time.sleep,
        ENGINE_READY_POLL_INTERVAL_SECONDS,
        ENGINE_READY_WARNING_INTERVAL_RETRIES,
    )


def _launch_local_daemon(local_daemon_script: Path | None, env: dict[str, str]) -> None:
    _launch_local_daemon_impl(
        local_daemon_script,
        env,
        logger,
        launch_background_process,
        time.sleep,
        LOCAL_DAEMON_SETTLE_SECONDS,
    )


def start() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with control_lock() as acquired:
        if not acquired:
            logger.info("Another control process is running. Skipping start.")
            return

        watcher_binary = _watcher_binary()
        current_watchers = get_pids(str(watcher_binary))
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
    watcher_pids = get_pids(str(_watcher_binary()))
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


_USAGE = "Usage: cortex-ctl [start|stop|restart|status|index-roots ...|knowledge ...|migrate ...|bootstrap ...]"


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
    if command == "index-roots":
        binary = resolve_rust_watcher_binary()
        completed = subprocess.run([str(binary), "index-roots", *args[1:]])
        return completed.returncode
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
