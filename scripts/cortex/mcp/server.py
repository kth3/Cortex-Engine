"""
Cortex MCP Server Main Entrypoint
"""
import sys
import json
import traceback
import os
import uuid
import threading
import time
from pathlib import Path

def configure_stdio():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import paths as pc_paths
from cortex import hooks_manager as pc_hooks
from cortex.mcp.context import McpContext
from cortex.mcp.response import create_text_response, create_error_response
from cortex.mcp.registry import list_tools

from cortex.mcp.tools.indexing import (
    call_pc_reindex, call_pc_index_status, call_pc_index_roots_list,
    call_pc_index_roots_add, call_pc_index_roots_remove
)
from cortex.mcp.tools.search import (
    call_pc_capsule, call_pc_skeleton, call_pc_impact_graph,
    call_pc_logic_flow, call_pc_run_pipeline
)
from cortex.mcp.tools.edit import (
    call_pc_read_with_hash, call_strict_replace
)
from cortex.mcp.tools.git import call_pc_git_log
from cortex.mcp.tools.memory import (
    call_save_observation, call_pc_memory_write, call_pc_memory_consolidate,
    call_pc_memory_read, call_pc_memory_search_knowledge
)
from cortex.mcp.tools.session import (
    call_pc_auto_context, call_pc_session_sync
)
from cortex.mcp.tools.orchestration import (
    call_todo_manager, call_create_contract
)

def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))

WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = str(uuid.uuid4())[:8]

CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)

def handle_request(req):
    m, p, rid = req.get("method"), req.get("params", {}), req.get("id")
    if m == "initialize": return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "Cortex-Hooks", "version": "3.8.0"}}}
    if m == "tools/list": return {"jsonrpc": "2.0", "id": rid, "result": {"tools": list_tools()}}
    if m == "tools/call":
        n, a = p.get("name"), p.get("arguments") or {}
        try:
            hook_msg = ""
            if n in ["pc_strict_replace", "pc_create_contract", "pc_todo_manager", "pc_capsule"]:
                guard_res = pc_hooks.dispatch(WORKSPACE, "before_tool_call", n, json.dumps(a))
                if guard_res and isinstance(guard_res, str):
                    if guard_res.startswith("Error:"):
                        return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Guard Blocked: {guard_res}"}]}}
                    elif guard_res.startswith("Info:"):
                        hook_msg = f"[{guard_res}]\n"
                    else:
                        hook_msg = f"[Hook: {guard_res}]\n"

            if n == "pc_reindex": r = call_pc_reindex(CTX, a)
            elif n == "pc_index_status": r = call_pc_index_status(CTX, a)
            elif n == "pc_index_roots_list": r = call_pc_index_roots_list(CTX, a)
            elif n == "pc_index_roots_add": r = call_pc_index_roots_add(CTX, a)
            elif n == "pc_index_roots_remove": r = call_pc_index_roots_remove(CTX, a)
            elif n == "pc_capsule": r = call_pc_capsule(CTX, a)
            elif n == "pc_skeleton": r = call_pc_skeleton(CTX, a)
            elif n == "pc_impact_graph": r = call_pc_impact_graph(CTX, a)
            elif n == "pc_logic_flow": r = call_pc_logic_flow(CTX, a)
            elif n == "pc_git_log": r = call_pc_git_log(CTX, a)
            elif n == "pc_run_pipeline": r = call_pc_run_pipeline(CTX, a)
            elif n == "pc_auto_context": r = call_pc_auto_context(CTX, a)
            elif n == "pc_read_with_hash": r = call_pc_read_with_hash(CTX, a)
            elif n == "pc_strict_replace": r = call_strict_replace(CTX, a)
            elif n == "pc_create_contract": r = call_create_contract(CTX, a)
            elif n == "pc_todo_manager": r = call_todo_manager(CTX, a)
            elif n == "pc_session_sync": r = call_pc_session_sync(CTX, a)
            elif n == "pc_memory_write": r = call_pc_memory_write(CTX, a)
            elif n == "pc_memory_consolidate": r = call_pc_memory_consolidate(CTX, a)
            elif n == "pc_memory_read": r = call_pc_memory_read(CTX, a)
            elif n == "pc_save_observation": r = call_save_observation(CTX, a)
            elif n == "pc_memory_search_knowledge": r = call_pc_memory_search_knowledge(CTX, a)
            else: return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown tool: {n}"}}
            
            return create_text_response(rid, r, hook_msg)
        except Exception as e: return create_error_response(rid, e)
    return {"jsonrpc": "2.0", "id": rid, "result": {}} if rid else None

def parent_watcher():
    """부모 프로세스 생존을 감시하는 데드맨 스위치 (다중 실행 시 충돌 방지 위해 직계 부모만 감시)"""
    try:
        import psutil
    except ImportError:
        return

    try:
        ppid = os.getppid()
        parent = psutil.Process(ppid)
    except Exception:
        os._exit(0)
    
    while True:
        try:
            if not parent.is_running() or parent.status() == psutil.STATUS_ZOMBIE:
                os._exit(0)
        except Exception:
            os._exit(0)
        
        try:
            time.sleep(2)
        except Exception:
            pass

def serve():
    watcher = threading.Thread(target=parent_watcher, daemon=True)
    watcher.start()

    try:
        import subprocess
        ctl_script = SCRIPTS_DIR / "cortex" / "cortex_ctl.py"
        if ctl_script.exists():
            subprocess.Popen(
                [sys.executable, str(ctl_script), "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
    except:
        pass

    try:
        while True:
            line = sys.stdin.readline()
            if not line: break
            try:
                req = json.loads(line)
                res = handle_request(req)
                if res: sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n"); sys.stdout.flush()
            except: pass
    finally:
        sys.stderr.write("[Cortex] MCP server terminated.\n")

def main():
    configure_stdio()
    serve()
