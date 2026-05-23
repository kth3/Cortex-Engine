"""Cortex MCP Server Main Entrypoint."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from cortex import paths as pc_paths
from cortex.mcp.context import McpContext
from cortex.mcp.dispatcher import handle_tools_call
from cortex.mcp.registry import list_tools
from cortex.mcp.server_support import (
    METHOD_INITIALIZE,
    METHOD_TOOLS_CALL,
    METHOD_TOOLS_LIST,
    SESSION_ID_LENGTH,
    TERMINATION_MESSAGE,
    initialize_result,
    jsonrpc_response,
    parent_watcher,
    reconfigure_stdio,
    request_parts,
    serve_stdin_loop,
    start_cortex_engine_if_available,
    start_parent_watcher_thread,
)


def _resolve_scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2]


SCRIPTS_DIR = _resolve_scripts_dir()
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))


def _new_session_id() -> str:
    return str(uuid.uuid4())[:SESSION_ID_LENGTH]


WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = _new_session_id()
CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)


def _tools_list_result():
    return {"tools": list_tools()}


def handle_request(req):
    method, params, rid = request_parts(req)

    if method == METHOD_INITIALIZE:
        return jsonrpc_response(rid, initialize_result())

    if method == METHOD_TOOLS_LIST:
        return jsonrpc_response(rid, _tools_list_result())

    if method == METHOD_TOOLS_CALL:
        return handle_tools_call(CTX, params, rid)

    return jsonrpc_response(rid, {}) if rid else None


def serve():
    def _run_parent_watcher() -> None:
        try:
            import psutil
        except ImportError:
            return
        parent_watcher(psutil)

    start_parent_watcher_thread(_run_parent_watcher)
    start_cortex_engine_if_available()
    try:
        serve_stdin_loop(handle_request)
    finally:
        sys.stderr.write(TERMINATION_MESSAGE)


def main():
    reconfigure_stdio()
    serve()


if __name__ == "__main__":
    main()
