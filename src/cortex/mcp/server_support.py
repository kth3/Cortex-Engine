"""Shared MCP server runtime helpers."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

STDIO_ENCODING = "utf-8"
JSONRPC_VERSION = "2.0"
METHOD_INITIALIZE = "initialize"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"
MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "Cortex-Hooks"
SERVER_VERSION = "3.8.0"
SESSION_ID_LENGTH = 8
PARENT_WATCH_INTERVAL_SECONDS = 2
TERMINATION_MESSAGE = "[Cortex] MCP server terminated.\n"


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding=STDIO_ENCODING)
        except Exception:
            pass


def initialize_result() -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }


def jsonrpc_response(rid, result):
    return {"jsonrpc": JSONRPC_VERSION, "id": rid, "result": result}


def request_parts(req):
    return req.get("method"), req.get("params", {}), req.get("id")


def parent_process_or_exit(psutil):
    try:
        ppid = os.getppid()
        return psutil.Process(ppid)
    except Exception:
        os._exit(0)


def parent_is_dead(parent, psutil) -> bool:
    return not parent.is_running() or parent.status() == psutil.STATUS_ZOMBIE


def sleep_parent_watch_interval() -> None:
    try:
        time.sleep(PARENT_WATCH_INTERVAL_SECONDS)
    except Exception:
        pass


def parent_watcher(psutil) -> None:
    parent = parent_process_or_exit(psutil)
    while True:
        try:
            if parent_is_dead(parent, psutil):
                os._exit(0)
        except Exception:
            os._exit(0)
        sleep_parent_watch_interval()


def start_cortex_engine_if_available() -> None:
    if os.environ.get("CORTEX_MCP_DISABLE_ENGINE_START") == "1":
        return
    try:
        import subprocess

        binary_name = "cortex-ctl.exe" if os.name == "nt" else "cortex-ctl"
        subprocess.Popen(
            [binary_name, "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def start_parent_watcher_thread(target) -> None:
    watcher = threading.Thread(target=target, daemon=True)
    watcher.start()


def write_response(res) -> None:
    if res:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def serve_stdin_loop(handle_request) -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            res = handle_request(req)
            write_response(res)
        except Exception:
            pass
