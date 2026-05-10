"""TCP IPC helpers for Cortex runtime control."""
from __future__ import annotations

import json
import socket
import struct

from .paths import ENGINE_HOST, ENGINE_PORT


def send_minimal_ping_status() -> str:
    """엔진 서버 ping 후 status 문자열 반환."""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect((ENGINE_HOST, ENGINE_PORT))
        data = json.dumps({"command": "ping"}).encode("utf-8")
        client.sendall(struct.pack("!I", len(data)) + data)
        header = client.recv(4)
        if not header:
            return "unreachable"
        size = struct.unpack("!I", header)[0]
        resp = client.recv(size).decode("utf-8")
        return json.loads(resp).get("status", "error")
    except Exception:
        return "unreachable"
    finally:
        try:
            client.close()
        except Exception:
            pass


def send_minimal_ping() -> bool:
    return send_minimal_ping_status() == "ok"
