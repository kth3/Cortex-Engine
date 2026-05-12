"""TCP port cleanup helpers for Cortex runtime control."""
from __future__ import annotations

import time
from collections.abc import Iterable

import psutil


PASSIVE_WAIT_STATUSES = frozenset({
    "LISTEN",
    "CLOSE_WAIT",
    "ESTABLISHED",
    "TIME_WAIT",
})

FORCE_KILL_STATUSES = frozenset({
    "LISTEN",
    "CLOSE_WAIT",
    "ESTABLISHED",
})

DEFAULT_PORT_RELEASE_TIMEOUT_SECONDS = 8.0
DEFAULT_PORT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_FORCE_KILL_WAIT_SECONDS = 3.0


def _connection_port(conn) -> int | None:
    """Return the local TCP port for a psutil connection, if available."""
    try:
        laddr = conn.laddr
        if not laddr:
            return None

        port = getattr(laddr, "port", None)
        if port is not None:
            return port

        if isinstance(laddr, tuple) and len(laddr) > 1:
            return laddr[1]

        return None
    except Exception:
        return None


def occupied_target_ports(
    target_ports: Iterable[int],
    current_pid: int,
    statuses: Iterable[str],
) -> list[tuple[int, int, str]]:
    """Return occupied target ports excluding the current process.

    반환 형식:
        [(port, pid, status), ...]
    """
    target_port_set = set(target_ports)
    status_set = set(statuses)
    occupied: list[tuple[int, int, str]] = []

    connections = psutil.net_connections(kind="tcp")

    for conn in connections:
        port = _connection_port(conn)
        if port not in target_port_set:
            continue
        if conn.status not in status_set:
            continue
        if not conn.pid or conn.pid == current_pid:
            continue
        occupied.append((port, conn.pid, conn.status))

    return occupied


def wait_for_ports_release(
    logger,
    target_ports: Iterable[int],
    current_pid: int,
    *,
    timeout_seconds: float = DEFAULT_PORT_RELEASE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_PORT_POLL_INTERVAL_SECONDS,
) -> None:
    """Wait until target TCP ports are no longer occupied.

    기존 cleanup_ports() 동작을 유지한다.
    포트가 계속 점유 중이면 warning 로그를 남기고 대기한다.
    timeout이 지나도 예외를 던지지 않는다.
    """
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            occupied = occupied_target_ports(
                target_ports,
                current_pid,
                PASSIVE_WAIT_STATUSES,
            )
        except Exception:
            occupied = []

        if not occupied:
            break

        logger.warning(f"포트 아직 점유 중: {occupied}. 재확인 대기...")
        time.sleep(poll_interval_seconds)


def force_release_ports(
    logger,
    target_ports: Iterable[int],
    current_pid: int,
    *,
    kill_wait_seconds: float = DEFAULT_FORCE_KILL_WAIT_SECONDS,
) -> None:
    """Force kill processes occupying target TCP ports.

    기존 force_cleanup_ports() 동작을 유지한다.
    NoSuchProcess, AccessDenied는 기존처럼 조용히 무시한다.
    전체 예외는 debug 로그로만 남긴다.
    """
    try:
        occupied = occupied_target_ports(
            target_ports,
            current_pid,
            FORCE_KILL_STATUSES,
        )

        for port, pid, _status in occupied:
            logger.warning(f"Port {port} still occupied by PID {pid}. Force killing...")
            try:
                process = psutil.Process(pid)
                process.kill()
                process.wait(timeout=kill_wait_seconds)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as exc:
        logger.debug(f"Port cleanup exception (non-critical): {exc}")
