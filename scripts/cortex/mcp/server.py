"""
Cortex MCP Server Main Entrypoint
"""
import sys
import json
import os
import uuid
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


def _reconfigure_stream(stream) -> None:
    try:
        stream.reconfigure(encoding=STDIO_ENCODING)
    except Exception:
        pass


def configure_stdio():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        _reconfigure_stream(stream)


def _resolve_scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2]


# 경로 설정
SCRIPTS_DIR = _resolve_scripts_dir()
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import paths as pc_paths
from cortex.mcp.context import McpContext
from cortex.mcp.registry import list_tools
from cortex.mcp.dispatcher import handle_tools_call


def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))


def _new_session_id() -> str:
    return str(uuid.uuid4())[:SESSION_ID_LENGTH]


WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = _new_session_id()

CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)


def _jsonrpc_response(rid, result):
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": rid,
        "result": result,
    }


def _initialize_result():
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }


def _tools_list_result():
    return {"tools": list_tools()}


def _request_parts(req):
    return req.get("method"), req.get("params", {}), req.get("id")


def handle_request(req):
    method, params, rid = _request_parts(req)

    if method == METHOD_INITIALIZE:
        return _jsonrpc_response(rid, _initialize_result())

    if method == METHOD_TOOLS_LIST:
        return _jsonrpc_response(rid, _tools_list_result())

    if method == METHOD_TOOLS_CALL:
        return handle_tools_call(CTX, params, rid)

    return _jsonrpc_response(rid, {}) if rid else None


def _parent_process_or_exit(psutil):
    try:
        ppid = os.getppid()
        return psutil.Process(ppid)
    except Exception:
        os._exit(0)


def _parent_is_dead(parent, psutil) -> bool:
    return not parent.is_running() or parent.status() == psutil.STATUS_ZOMBIE


def _sleep_parent_watch_interval() -> None:
    try:
        time.sleep(PARENT_WATCH_INTERVAL_SECONDS)
    except Exception:
        pass


def parent_watcher():
    """부모 프로세스 생존을 감시하는 데드맨 스위치 (다중 실행 시 충돌 방지 위해 직계 부모만 감시)"""
    try:
        import psutil
    except ImportError:
        return

    parent = _parent_process_or_exit(psutil)

    while True:
        try:
            if _parent_is_dead(parent, psutil):
                os._exit(0)
        except Exception:
            os._exit(0)

        _sleep_parent_watch_interval()


def _start_cortex_engine_if_available() -> None:
    try:
        import subprocess

        subprocess.Popen(
            [sys.executable, "-m", "cortex.runtime.cli", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except:
        pass


def _start_parent_watcher_thread() -> None:
    watcher = threading.Thread(target=parent_watcher, daemon=True)
    watcher.start()


def _write_response(res) -> None:
    if res:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _serve_stdin_loop() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            res = handle_request(req)
            _write_response(res)
        except:
            pass


def serve():
    _start_parent_watcher_thread()
    _start_cortex_engine_if_available()

    try:
        _serve_stdin_loop()
    finally:
        sys.stderr.write(TERMINATION_MESSAGE)


def main():
    configure_stdio()
    serve()
