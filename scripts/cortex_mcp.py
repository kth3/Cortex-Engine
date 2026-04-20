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
import subprocess
import shlex
from pathlib import Path

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

# 모듈화된 로직 임포트
from cortex import db as pc_db
from cortex import indexer as pc_indexer
from cortex import capsule as pc_capsule_mod
from cortex import graph_db as pc_graph_db
from cortex import skeleton as pc_skeleton_mod
from cortex import git_analyzer as pc_git_mod
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex.persistent_memory import PersistentMemoryManager
from cortex.skill_manager import SkillManager
from cortex.orchestrator import manage_todo, create_contract
from cortex.edit_engine import read_with_hash, strict_replace

def _find_real_workspace(start_path):
    curr = start_path.resolve()
    parts = curr.parts
    if ".agents" in parts:
        idx = parts.index(".agents")
        return str(Path(*parts[:idx]))
    for _ in range(5):
        if (curr / ".git").exists() or (curr / ".agents").exists():
            return str(curr)
        if curr.parent == curr: break
        curr = curr.parent
    return str(SCRIPTS_DIR.parent.parent)

WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = str(uuid.uuid4())[:8]

# 싱글톤 인스턴스
_storage = None
_skills = None

def get_storage():
    global _storage
    if _storage is None: _storage = PersistentMemoryManager(WORKSPACE)
    return _storage

def get_skills():
    global _skills
    if _skills is None: _skills = SkillManager(WORKSPACE)
    return _skills


# ==============================================================================
# MCP TOOL HANDLERS (Delegation with Hooks)
# ==============================================================================

def call_todo_manager(args):
    return manage_todo(WORKSPACE, args["action"], args.get("task"), args.get("task_id"))

def call_strict_replace(args):
    res = strict_replace(WORKSPACE, args["file_path"], args["old_content"], args["new_content"])
    if "success" in res:
        # [Lifecycle Hook] After Edit Hook 실행
        hook_feedback = pc_hooks.dispatch(WORKSPACE, "after_edit", os.path.join(WORKSPACE, args["file_path"]))
        if hook_feedback: res["hook_feedback"] = hook_feedback
        
        pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "edit", f"Strict edit: {args['file_path']}", [args['file_path']])
        
        # [Lifecycle Hook] After Save Observation (자동 추출)
        pc_hooks.dispatch(WORKSPACE, "after_save_observation")
    return res

def call_create_contract(args):
    res = create_contract(WORKSPACE, SESSION_ID, args["lane_id"], args["task_name"], args["instructions"], args.get("files_to_modify"))
    pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "decision", f"Contract created: {res['contract_id']}", [res['path']])
    pc_hooks.dispatch(WORKSPACE, "after_save_observation")
    return res

def call_save_observation(args):
    res = pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, args.get("obs_type", "insight"), args["content"], args.get("file_paths", []))
    pc_hooks.dispatch(WORKSPACE, "after_save_observation")
    return res

# ==============================================================================
# TOOL REGISTRY
# ==============================================================================

TOOLS = [
    {"name": "pc_reindex", "description": "인덱싱 실행", "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean"}}}},
    {"name": "pc_index_status", "description": "인덱스 상태", "inputSchema": {"type": "object"}},
    {"name": "pc_capsule", "description": "1순위 검색", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "pc_auto_explore", "description": "자율 다단계 탐색", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "pc_read_with_hash", "description": "해시 포함 읽기", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
    {"name": "pc_strict_replace", "description": "정밀 편집", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_content": {"type": "string"}, "new_content": {"type": "string"}}, "required": ["file_path", "old_content", "new_content"]}},
    {"name": "pc_create_contract", "description": "계약 생성", "inputSchema": {"type": "object", "properties": {"lane_id": {"type": "string"}, "task_name": {"type": "string"}, "instructions": {"type": "string"}}, "required": ["lane_id", "task_name", "instructions"]}},
    {"name": "pc_todo_manager", "description": "Todo 관리", "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}},
    {"name": "pc_memory_write", "description": "지식 저장", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}}, "required": ["key", "category", "content"]}},
    {"name": "pc_memory_read", "description": "지식 조회", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "pc_save_observation", "description": "인사이트 저장", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "pc_memory_search_knowledge", "description": "영구 지식, 규칙 및 스킬 하이브리드 검색", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}}, "required": ["query"]}},
    {"name": "pc_run_background_task", "description": "백그라운드 태스크 실행", "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "lane_id": {"type": "string"}, "task_name": {"type": "string"}}, "required": ["command", "lane_id", "task_name"]}}
]

def handle_request(req):
    m, p, rid = req.get("method"), req.get("params", {}), req.get("id")
    if m == "initialize": return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "Cortex-Hooks", "version": "3.8.0"}}}
    if m == "tools/list": return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
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

            if n == "pc_reindex": r = pc_indexer.index_workspace(WORKSPACE, force=a.get("force", False))
            elif n == "pc_index_status": 
                conn = pc_db.get_connection(WORKSPACE); r = pc_db.get_stats(conn); conn.close()
            elif n == "pc_capsule": r = pc_capsule_mod.generate_context_capsule(WORKSPACE, a["query"])
            elif n == "pc_auto_explore": r = pc_capsule_mod.generate_context_capsule(WORKSPACE, a["query"])
            elif n == "pc_read_with_hash": r = read_with_hash(WORKSPACE, a["file_path"])
            elif n == "pc_strict_replace": r = call_strict_replace(a)
            elif n == "pc_create_contract": r = call_create_contract(a)
            elif n == "pc_todo_manager": r = call_todo_manager(a)
            elif n == "pc_memory_write": r = get_storage().write("default", {"key": a["key"], "category": a["category"], "content": a["content"]})
            elif n == "pc_memory_read": r = get_storage().read("default", a["key"])
            elif n == "pc_save_observation": r = call_save_observation(a)
            elif n == "pc_memory_search_knowledge":
                from cortex import vector_engine as ve
                raw_res = get_storage().search_knowledge(a["query"], category=a.get("category"), limit=5, ve_module=ve)
                r = json.dumps(raw_res, ensure_ascii=False, indent=2)
            elif n == "pc_run_background_task":
                cmd = a["command"]
                lid = a["lane_id"]
                tname = a["task_name"]
                relay_script = SCRIPTS_DIR / "relay.py"

                # Fix #5: relay acquire
                acq_result = subprocess.run(
                    [sys.executable, str(relay_script), "acquire", SESSION_ID, tname, lid],
                    capture_output=True, text=True
                )
                if acq_result.returncode != 0:
                    r = {"status": "error", "reason": f"relay acquire failed: {acq_result.stdout.strip()}"}
                else:
                    # Fix #5: shlex.quote 적용 및 release 인자 순서 정상화
                    # relay.py release [aid] [lid] [hto] [msg] [cid]
                    release_cmd = " ".join([
                        shlex.quote(sys.executable),
                        shlex.quote(str(relay_script)),
                        "release",
                        shlex.quote(SESSION_ID),
                        shlex.quote(lid),
                        "''", # handoff_to
                        "''", # message
                        "''"  # contract_id
                    ])
                    wrapped_cmd = f"( {cmd} ); {release_cmd}"
                    # Fix: PIPE 버퍼(64KB) 초과 → 프로세스 Hang → 좀비 락 연쇄 문제 해결
                    # stdout/stderr를 로그 파일로 리디렉션하여 버퍼 막힘을 원천 차단
                    # (PIPE는 읽지 않으면 64KB에서 블로킹 → 백그라운드 Hang → 락 반환 불가)
                    import datetime as _dt
                    _log_dir = Path(WORKSPACE) / ".agents" / "history"
                    _log_dir.mkdir(parents=True, exist_ok=True)
                    _ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    _log_path = _log_dir / f"task_{lid}_{_ts}.log"
                    with open(_log_path, "w", encoding="utf-8") as _log_fh:
                        proc = subprocess.Popen(
                            wrapped_cmd,
                            shell=True,
                            stdout=_log_fh,
                            stderr=_log_fh,
                            start_new_session=True,
                        )
                    r = {"status": "started", "pid": proc.pid, "lane": lid,
                         "log": f".agents/history/task_{lid}_{_ts}.log",
                         "note": "relay lock will be auto-released on completion"}
            else: return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown tool: {n}"}}
            
            if isinstance(r, (dict, list)):
                final_res = json.dumps(r, ensure_ascii=False, indent=2)
            else:
                final_res = str(r)
            if hook_msg:
                final_res = f"{hook_msg}\n{final_res}"
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": final_res}]}}
        except Exception as e: return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}]}}
    return {"jsonrpc": "2.0", "id": rid, "result": {}} if rid else None

def start_watcher_daemon():
    try:
        watcher_script = SCRIPTS_DIR / "cortex" / "watcher.py"
        log_file = Path(WORKSPACE) / ".agents" / "history" / "watcher.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 중복 실행 방지
        subprocess.run("pkill -f watcher.py", shell=True, stderr=subprocess.DEVNULL)
        
        if watcher_script.exists():
            with open(log_file, "a", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    [sys.executable, str(watcher_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return proc
    except Exception as e:
        sys.stderr.write(f"Failed to start watcher daemon: {e}\n")
    return None

def serve():
    watcher_proc = start_watcher_daemon()
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
        if watcher_proc:
            watcher_proc.terminate()
            sys.stderr.write("[Cortex] Watcher terminated along with MCP server.\n")

if __name__ == "__main__":
    serve()
