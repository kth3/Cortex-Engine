"""Shared implementation helpers for Cortex runtime control."""
from __future__ import annotations

import os
from pathlib import Path

from .paths import ENGINE_HOST, ENGINE_PORT, LOG_DIR, SERVER_SCRIPT, resolve_rust_watcher_binary


def resolve_watcher_binary() -> Path:
    return resolve_rust_watcher_binary()


def resolve_start_timeout() -> int:
    """Return the configured startup wait budget in seconds."""
    raw = (os.environ.get("CORTEX_START_TIMEOUT") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 35


def service_scripts(cortex_home: Path, resolve_local_daemon_script, watcher_binary: Path) -> list[tuple[Path, str]]:
    scripts = [(SERVER_SCRIPT, "Engine Server"), (watcher_binary, "Watcher")]
    local_daemon_script = resolve_local_daemon_script(cortex_home)
    if local_daemon_script:
        scripts.append((local_daemon_script, "Local Daemon"))
    return scripts


def cleanup_runtime_logs(log_dir: Path, logger) -> None:
    for log_name in ("watcher_output.log", "engine_server.log"):
        target = log_dir / log_name
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
            logger.info(f"Infrastructure Cleaned: Removed {log_name}")


def is_local_daemon_running(local_daemon_script: Path | None, get_pids) -> bool:
    if not local_daemon_script:
        return True
    return bool(get_pids(str(local_daemon_script)))


def wait_for_engine_ready(
    server_proc,
    logger,
    resolve_start_timeout_fn,
    send_minimal_ping_status,
    sleep_fn,
    poll_interval_seconds: int,
    warning_interval_retries: int,
) -> bool:
    """Poll the engine for readiness up to the configured timeout."""
    max_retries = resolve_start_timeout_fn()
    logger.info(
        f"Waiting for Engine Server to initialize GPU (timeout {max_retries}s, "
        "CORTEX_START_TIMEOUT to override)..."
    )

    last_status = "unreachable"
    for retry in range(max_retries):
        if server_proc.poll() is not None:
            logger.error(
                f"CRITICAL: Engine Server crashed during startup (code={server_proc.returncode})."
            )
            return False

        last_status = send_minimal_ping_status()
        if last_status == "ok":
            return True

        if retry > 0 and retry % warning_interval_retries == 0:
            logger.warning(
                f"Engine Server not ready yet (status={last_status}, "
                f"retry {retry}/{max_retries})..."
            )
        sleep_fn(poll_interval_seconds)

    if last_status == "loading":
        logger.info(
            "Engine Server is still loading in background after "
            f"{max_retries}s. Run 'cortex-ctl status' to track readiness, "
            "or set CORTEX_START_TIMEOUT to wait longer synchronously."
        )
        return True

    logger.error(
        f"CRITICAL: Engine Server failed to become ready (last status={last_status}). "
        "Check cortex.log."
    )
    return False


def launch_local_daemon(
    local_daemon_script: Path | None,
    env: dict[str, str],
    logger,
    launch_background_process,
    sleep_fn,
    settle_seconds: int,
) -> None:
    if not local_daemon_script:
        return

    logger.info(f"Launching Local Daemon: {local_daemon_script}")
    daemon_proc = launch_background_process(local_daemon_script, env)
    sleep_fn(settle_seconds)
    if daemon_proc.poll() is not None:
        logger.error(
            f"Local Daemon exited immediately (code={daemon_proc.returncode}). "
            "Check local daemon logs or configuration."
        )
    else:
        logger.info("Local Daemon started successfully.")


def perform_stop(
    *,
    logger,
    service_scripts,
    get_pids,
    request_graceful_stop,
    terminate_pid,
    cleanup_ports,
    force_cleanup_ports,
    os_getpid,
    sleep_fn,
    stop_port_release_grace_seconds: int,
) -> None:
    logger.info("Stopping all Cortex services...")

    all_pids: list[int] = []
    for script, label in service_scripts:
        pids = get_pids(str(script))
        if pids:
            for pid in pids:
                logger.info(f"Terminating {label} (PID: {pid})...")
                if request_graceful_stop(pid):
                    all_pids.append(pid)
        else:
            logger.info(f"{label} is not running.")

    if all_pids:
        for pid in all_pids:
            terminate_pid(pid, logger)

        sleep_fn(stop_port_release_grace_seconds)
        cleanup_ports(logger, os_getpid())

    force_cleanup_ports(logger, os_getpid())

    logger.info(f"IPC Endpoint: {ENGINE_HOST}:{ENGINE_PORT} (TCP — no file cleanup needed)")

    cleanup_runtime_logs(LOG_DIR, logger)

    logger.info("All services stop/cleanup sequence complete.")
