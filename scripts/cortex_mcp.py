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
from cortex import git_analyzer as pc_git_mod
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex.persistent_memory import PersistentMemoryManager
from cortex.skill_manager import SkillManager
from cortex.orchestrator import manage_todo, create_contract
from cortex.edit_engine import read_with_hash, strict_replace, record_edit_event
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

CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)

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
    file_path = args["file_path"]
    try:
        full_path_obj = (Path(WORKSPACE) / file_path).resolve()
        full_path_obj.relative_to(Path(WORKSPACE).resolve())
        full_path = str(full_path_obj)
    except Exception as e:
        return {"error": f"File path validation failed before edit: {e}"}

    before_content = None
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            before_content = f.read()
    except Exception as e:
        return {"error": f"File read before edit failed: {e}"}

    res = strict_replace(WORKSPACE, file_path, args["old_content"], args["new_content"])
    if "success" in res:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                after_content = f.read()
            conn = pc_db.get_connection(WORKSPACE)
            try:
                pc_db.init_schema(conn)
                record_edit_event(
                    conn,
                    workspace=WORKSPACE,
                    file_path=file_path,
                    before_content=before_content,
                    after_content=after_content,
                    session_id=SESSION_ID,
                    event_source="cortex_mcp",
                    tool_name="pc_strict_replace",
                    edit_summary=f"Strict edit: {file_path}",
                )
            finally:
                conn.close()
        except Exception as e:
            # 편집은 이미 디스크에 반영된 뒤이므로, 감사/관측용 DB 기록 실패를 편집 실패로
            # 되돌리면 실제 상태와 응답이 어긋난다. success는 유지하고 별도 필드로 노출해
            # 운영자가 로깅 경로 문제만 분리해서 추적할 수 있게 한다.
            res["event_log_error"] = str(e)

        # [Lifecycle Hook] After Edit Hook 실행
        hook_feedback = pc_hooks.dispatch(WORKSPACE, "after_edit", os.path.join(WORKSPACE, file_path))
        if hook_feedback: res["hook_feedback"] = hook_feedback
        
        pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "edit", f"Strict edit: {file_path}", [file_path])
        
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





def _append_markdown_with_archive(target_filename, content):
    import datetime
    import shutil
    md_path = str(pc_paths.history_dir(WORKSPACE) / target_filename)
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = str(pc_paths.history_dir(WORKSPACE) / "archive")
        os.makedirs(archive_dir, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name_part, ext = os.path.splitext(target_filename)
        archive_path = os.path.join(archive_dir, f"{name_part}_{now_str}{ext}")
        shutil.move(md_path, archive_path)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(content)

def call_pc_memory_write(args):
    key = args["key"]
    category = args["category"]
    content = args["content"]
    data = {
        "key": key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    ok = get_storage().write("default", data)
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule", "protocol"]:
        target_file = "patterns.md"
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {key}\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {"success": ok, "key": key, "auto_promoted_to": target_file}

def call_pc_memory_consolidate(args):
    """파편 메모리 병합. dry_run 기본 True — 사용자 승인 없는 자동 삭제 방지."""
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    dry_run = args.get("dry_run", True)

    would_delete = list(old_keys)
    would_write = {
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule"]:
        target_file = "patterns.md"

    if dry_run:
        return {
            "executed": False,
            "would_delete": would_delete,
            "would_write": would_write,
            "auto_promoted_to": target_file,
            "note": "dry_run=true (default). 실제 병합·삭제 없음. 실행하려면 dry_run=false 명시.",
        }

    st = get_storage()
    deleted_count = st.delete_many("default", old_keys)
    ok = st.write("default", would_write)
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {new_key} (Consolidated from {len(old_keys)} items)\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {
        "executed": True,
        "success": ok,
        "consolidated_key": new_key,
        "deleted_old_fragments": deleted_count,
        "auto_promoted_to": target_file,
        "would_delete": would_delete,
        "would_write": would_write,
    }

def call_pc_session_sync(args):
    import re
    import yaml
    task_desc = args["task_desc"]
    branch = "unknown"
    jira_issues = []
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE).decode().strip()
        match = re.search(r'([A-Z0-9]+-\d+)', branch)
        if match:
            jira_issues.append(match.group(1))
    except:
        pass
    modified_files = []
    try:
        status1 = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=WORKSPACE).decode().strip().split('\n')
        status2 = subprocess.check_output(["git", "log", "-n", "3", "--name-only", "--pretty=format:"], cwd=WORKSPACE).decode().strip().split('\n')
        combined = [f for f in status1 + status2 if f]
        seen = set()
        for f in combined:
            if f not in seen:
                seen.add(f)
                modified_files.append(f)
    except:
        pass
    relationships = {
        "jira_issues": jira_issues,
        "modifies": modified_files[:10],
        "branch": branch
    }
    key = f"session-sync-{SESSION_ID}"
    data = {
        "key": key,
        "category": "decision",
        "content": task_desc,
        "tags": ["session-sync", "auto-generated", "autonomous-rag"],
        "relationships": relationships
    }
    ok = get_storage().write("default", data)
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"\n- [CONFIRMED] **[SESSION_SYNC]** {now_str} | Branch: {branch} | Issue: {jira_issues}\n  - 📝 {task_desc}\n  - 📂 Modifies: {len(modified_files)} files\n"
    _append_markdown_with_archive("inbox.md", log_line)
    yaml_path = str(pc_paths.history_dir(WORKSPACE) / "memory.yaml")
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, 'r', encoding='utf-8') as yf:
                yaml_data = yaml.safe_load(yf) or {}
            yaml_data['active_branch'] = branch
            yaml_data['last_sync'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(yaml_path, 'w', encoding='utf-8') as yf:
                yaml.dump(yaml_data, yf, allow_unicode=True, sort_keys=False)
        except Exception:
            pass
    return {"success": ok, "key": key, "extracted_relationships": relationships, "markdown_synced": True}

def _append_markdown_with_archive(target_filename, content):
    import datetime
    import shutil
    md_path = str(pc_paths.history_dir(WORKSPACE) / target_filename)
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = str(pc_paths.history_dir(WORKSPACE) / "archive")
        os.makedirs(archive_dir, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name_part, ext = os.path.splitext(target_filename)
        archive_path = os.path.join(archive_dir, f"{name_part}_{now_str}{ext}")
        shutil.move(md_path, archive_path)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(content)

def call_pc_memory_write(args):
    key = args["key"]
    category = args["category"]
    content = args["content"]
    data = {
        "key": key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    ok = get_storage().write("default", data)
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule", "protocol"]:
        target_file = "patterns.md"
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {key}\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {"success": ok, "key": key, "auto_promoted_to": target_file}

def call_pc_memory_consolidate(args):
    """파편 메모리 병합. dry_run 기본 True — 사용자 승인 없는 자동 삭제 방지."""
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    dry_run = args.get("dry_run", True)

    would_delete = list(old_keys)
    would_write = {
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule"]:
        target_file = "patterns.md"

    if dry_run:
        return {
            "executed": False,
            "would_delete": would_delete,
            "would_write": would_write,
            "auto_promoted_to": target_file,
            "note": "dry_run=true (default). 실제 병합·삭제 없음. 실행하려면 dry_run=false 명시.",
        }

    st = get_storage()
    deleted_count = st.delete_many("default", old_keys)
    ok = st.write("default", would_write)
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {new_key} (Consolidated from {len(old_keys)} items)\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {
        "executed": True,
        "success": ok,
        "consolidated_key": new_key,
        "deleted_old_fragments": deleted_count,
        "auto_promoted_to": target_file,
        "would_delete": would_delete,
        "would_write": would_write,
    }

def call_pc_session_sync(args):
    import re
    import yaml
    task_desc = args["task_desc"]
    branch = "unknown"
    jira_issues = []
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE).decode().strip()
        match = re.search(r'([A-Z0-9]+-\d+)', branch)
        if match:
            jira_issues.append(match.group(1))
    except:
        pass
    modified_files = []
    try:
        status1 = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=WORKSPACE).decode().strip().split('\n')
        status2 = subprocess.check_output(["git", "log", "-n", "3", "--name-only", "--pretty=format:"], cwd=WORKSPACE).decode().strip().split('\n')
        combined = [f for f in status1 + status2 if f]
        seen = set()
        for f in combined:
            if f not in seen:
                seen.add(f)
                modified_files.append(f)
    except:
        pass
    relationships = {
        "jira_issues": jira_issues,
        "modifies": modified_files[:10],
        "branch": branch
    }
    key = f"session-sync-{SESSION_ID}"
    data = {
        "key": key,
        "category": "decision",
        "content": task_desc,
        "tags": ["session-sync", "auto-generated", "autonomous-rag"],
        "relationships": relationships
    }
    ok = get_storage().write("default", data)
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"\n- [CONFIRMED] **[SESSION_SYNC]** {now_str} | Branch: {branch} | Issue: {jira_issues}\n  - 📝 {task_desc}\n  - 📂 Modifies: {len(modified_files)} files\n"
    _append_markdown_with_archive("inbox.md", log_line)
    yaml_path = str(pc_paths.history_dir(WORKSPACE) / "memory.yaml")
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, 'r', encoding='utf-8') as yf:
                yaml_data = yaml.safe_load(yf) or {}
            yaml_data['active_branch'] = branch
            yaml_data['last_sync'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(yaml_path, 'w', encoding='utf-8') as yf:
                yaml.dump(yaml_data, yf, allow_unicode=True, sort_keys=False)
        except Exception:
            pass
    return {"success": ok, "key": key, "extracted_relationships": relationships, "markdown_synced": True}



def call_pc_git_log(args):
    try:
        history = pc_git_mod.get_file_history(WORKSPACE, args["file_path"], args.get("limit", 5))
        return history
    except Exception as e:
        return {"error": str(e)}

def call_pc_auto_context(args):
    token_budget = args.get("token_budget", 2000)
    conn = pc_db.get_connection(WORKSPACE)
    try:
        sections = []
        total_chars = 0
        
        # 1. 최신 decisions
        rows = conn.execute(
            "SELECT key, content, updated_at FROM memories WHERE category = 'decision' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:150]
            entry = f"[decision] {d['key']}: {snippet}"
            if total_chars + len(entry) > token_budget: break
            sections.append(entry)
            total_chars += len(entry)

        # 2. 최신 patterns
        rows = conn.execute(
            "SELECT key, content, updated_at FROM memories WHERE category = 'pattern' ORDER BY updated_at DESC LIMIT 3"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:150]
            entry = f"[pattern] {d['key']}: {snippet}"
            if total_chars + len(entry) > token_budget: break
            sections.append(entry)
            total_chars += len(entry)

        # 3. 인기 항목 (access_count)
        rows = conn.execute(
            "SELECT key, category, content, access_count FROM memories WHERE access_count > 0 ORDER BY access_count DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:100]
            entry = f"[{d['category']}] {d['key']} (hits:{d['access_count']}): {snippet}"
            if total_chars + len(entry) > token_budget: break
            if not any(d["key"] in s for s in sections):
                sections.append(entry)
                total_chars += len(entry)

        # 추가: HANDOFF 상태 레인의 contract 확인
        board_path = pc_paths.data_dir(WORKSPACE) / "state" / "board.json"
        if board_path.exists():
            try:
                board = json.loads(board_path.read_text(encoding="utf-8"))
                for lid, lane in board.get("lanes", {}).items():
                    if lane.get("contract_id"):
                        entry = f"[contract] lane={lid}: {lane['contract_id']}"
                        sections.append(entry)
                        total_chars += len(entry)
            except Exception:
                pass

        return {
            "context": "\n".join(sections),
            "totalChars": total_chars,
            "itemCount": len(sections)
        }
    finally:
        conn.close()

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
            elif n == "pc_git_log": r = call_pc_git_log(a)
            elif n == "pc_run_pipeline": r = call_pc_run_pipeline(CTX, a)
            elif n == "pc_auto_context": r = call_pc_auto_context(a)
            elif n == "pc_read_with_hash": r = read_with_hash(WORKSPACE, a["file_path"])
            elif n == "pc_strict_replace": r = call_strict_replace(a)
            elif n == "pc_create_contract": r = call_create_contract(a)
            elif n == "pc_todo_manager": r = call_todo_manager(a)
            elif n == "pc_session_sync": r = call_pc_session_sync(a)
            elif n == "pc_memory_write": r = call_pc_memory_write(a)
            elif n == "pc_memory_consolidate": r = call_pc_memory_consolidate(a)
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
                    _log_dir = pc_paths.history_dir(WORKSPACE)
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
