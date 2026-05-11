"""TCP IPC helpers for Cortex runtime control."""
from __future__ import annotations

import json
import socket
import struct
from typing import Any

from .paths import ENGINE_HOST, ENGINE_PORT


def recv_exact(sock: socket.socket, size: int) -> bytes | None:
    """Receive exactly size bytes or return None if the stream closes first."""
    data = b""
    while len(data) < size:
        chunk = sock.recv(min(size - len(data), 4096))
        if not chunk:
            return None
        data += chunk
    return data


def recv_msg(sock: socket.socket) -> dict[str, Any] | None:
    """Receive one length-prefixed JSON message."""
    header = recv_exact(sock, 4)
    if not header:
        return None
    size = struct.unpack("!I", header)[0]
    data = recv_exact(sock, size)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def send_msg(sock: socket.socket, msg: dict[str, Any]) -> None:
    """Send one length-prefixed JSON message."""
    data = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)


def send_request(
    msg: dict[str, Any],
    *,
    host: str = ENGINE_HOST,
    port: int = ENGINE_PORT,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """Send a request to a Cortex TCP endpoint and return its response."""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        client.connect((host, port))
        send_msg(client, msg)
        return recv_msg(client)
    finally:
        client.close()


def send_minimal_ping_status() -> str:
    """엔진 서버 ping 후 status 문자열 반환."""
    try:
        response = send_request({"command": "ping"})
        if not response:
            return "unreachable"
        return response.get("status", "error")
    except Exception:
        return "unreachable"


def send_minimal_ping() -> bool:
    return send_minimal_ping_status() == "ok"
