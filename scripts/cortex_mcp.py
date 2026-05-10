#!/usr/bin/env python3
"""
Cortex MCP Server (v3.8: Hook-Integrated Modular Architecture)
엔진 복잡성 분리 및 런타임 생명주기 훅 시스템 통합.
"""
import sys
import json
import traceback
import os
import uuid
from pathlib import Path
import threading
import time

# Windows 호환성을 위한 표준 입출력 UTF-8 강제 설정
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.stdin.reconfigure(encoding='utf-8')

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

# 모듈화된 로직 임포트
from cortex import db as pc_db
from cortex import indexer as pc_indexer
from cortex import capsule as pc_capsule_mod
from cortex import graph_db as pc_graph_db
from cortex import skeleton as pc_skeleton_mod
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex.skill_manager import SkillManager
from cortex.search_engine import unified_pipeline_search
from cortex import vector_engine as ve
from cortex import paths as pc_paths
from cortex.indexer_utils import load_settings, scan_files
from cortex.mcp.response import create_text_response, create_error_response

def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))

WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = str(uuid.uuid4())[:8]

from cortex.mcp.context import McpContext
from cortex.mcp.tools.indexing import (
    call_pc_reindex,
    call_pc_index_status,
    call_pc_index_roots_list,
    call_pc_index_roots_add,
    call_pc_index_roots_remove,
)
from cortex.mcp.tools.search import (
    call_pc_capsule,
    call_pc_skeleton,
    call_pc_impact_graph,
    call_pc_logic_flow,
    call_pc_run_pipeline,
)
from cortex.mcp.tools.edit import (
    call_pc_read_with_hash,
    call_strict_replace,
)
from cortex.mcp.tools.git import call_pc_git_log
from cortex.mcp.tools.memory import (
    call_save_observation,
    call_pc_memory_write,
    call_pc_memory_consolidate,
    call_pc_memory_read,
    call_pc_memory_search_knowledge,
)
from cortex.mcp.tools.session import (
    call_pc_auto_context,
    call_pc_session_sync,
)
from cortex.mcp.tools.orchestration import (
    call_todo_manager,
    call_create_contract,
)

CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)

# 싱글톤 인스턴스
_storage = None
_skills = None

def get_skills():
    global _skills
    if _skills is None: _skills = SkillManager(WORKSPACE)
    return _skills

# ==============================================================================
# TOOL REGISTRY
# ==============================================================================

from cortex.mcp.registry import list_tools

def handle_request(req):
    m, p, rid = req.get("method"), req.get("params", {}), req.get("id")
    if m == "initialize": return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "Cortex-Hooks", "version": "3.8.0"}}}
    if m == "tools/list": return {"jsonrpc": "2.0", "id": rid, "result": {"tools": list_tools()}}
    if m == "tools/call":
        n, a = p.get("name"), p.get("arguments") or {}
        try:
            # [Lifecycle Hook] Before Tool Call (Preventive Guard)
            # 위험한 도구 호출 전 자율 검증 및 키워드 기반 정보 제공
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

            # [Opportunistic Indexing] 동기식 인덱싱 호출 부분 삭제 (백그라운드 Watcher 데몬으로 위임됨)

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
        # [Auto-Start] 에디터가 MCP 서버를 올리는 즉시 Cortex 인프라 동기화 (백그라운드)
        import subprocess
        from pathlib import Path
        ctl_script = Path(__file__).parent / "cortex" / "cortex_ctl.py"
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

if __name__ == "__main__":
    serve()
