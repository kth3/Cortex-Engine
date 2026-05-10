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

def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))

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


def _read_local_settings():
    import yaml
    _, local_path = pc_paths.settings_paths(WORKSPACE)
    if not local_path.exists():
        return {}, local_path
    with open(local_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}, local_path


def _write_local_settings(data, local_path):
    import yaml
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _effective_index_roots(settings):
    rules = settings.get("indexing_rules", {}) or {}
    roots = rules.get("index_roots")
    if roots is None:
        roots = ["."]
    if isinstance(roots, str):
        roots = [roots]
    return list(dict.fromkeys(roots or []))


def _validated_index_root(raw_path):
    if not raw_path or not str(raw_path).strip():
        raise ValueError("index root path is required")
    if any(ch in str(raw_path) for ch in "*?"):
        raise ValueError("glob patterns are not allowed for index_roots")

    ws = Path(WORKSPACE).resolve()
    raw = Path(str(raw_path).strip()).expanduser()
    target = raw.resolve() if raw.is_absolute() else (ws / raw).resolve()
    target.relative_to(ws)
    rel = target.relative_to(ws)
    rel_text = "." if str(rel) == "." else str(rel).replace("\\", "/")

    dangerous = {".git", "node_modules", "library", "temp"}
    parts = {p.lower() for p in Path(rel_text).parts}
    if rel_text != "." and parts & dangerous:
        raise ValueError("dangerous index root rejected")
    return rel_text


def _index_roots_scan_count(candidate_roots):
    settings = load_settings(WORKSPACE)
    settings.setdefault("indexing_rules", {})["index_roots"] = candidate_roots
    return len(scan_files(WORKSPACE, pc_indexer.SUPPORTED_EXTENSIONS, settings_override=settings))


def call_pc_index_roots_list(args):
    settings = load_settings(WORKSPACE)
    roots = _effective_index_roots(settings)
    ws = Path(WORKSPACE).resolve()
    resolved = []
    for root in roots:
        target = ws if root == "." else (ws / root).resolve()
        resolved.append({"path": root, "absolute": str(target), "exists": target.exists()})
    _, local_path = pc_paths.settings_paths(WORKSPACE)
    return {"index_roots": roots, "resolved": resolved, "settings_local": str(local_path)}


def call_pc_index_roots_add(args):
    dry_run = args.get("dry_run", True)
    root = _validated_index_root(args["path"])
    local_settings, local_path = _read_local_settings()
    roots = _effective_index_roots(load_settings(WORKSPACE))
    if root not in roots:
        roots.append(root)
    scan_count = _index_roots_scan_count(roots)
    if not dry_run:
        local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
        _write_local_settings(local_settings, local_path)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}


def call_pc_index_roots_remove(args):
    dry_run = args.get("dry_run", True)
    root = _validated_index_root(args["path"])
    local_settings, local_path = _read_local_settings()
    roots = [r for r in _effective_index_roots(load_settings(WORKSPACE)) if r != root]
    scan_count = _index_roots_scan_count(roots)
    if not dry_run:
        local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
        _write_local_settings(local_settings, local_path)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}


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

def call_pc_impact_graph(args):
    fqn = args["fqn"]
    direction = args.get("direction", "both")
    max_depth = args.get("max_depth", 2)
    max_nodes = args.get("max_nodes", 50)
    conn = pc_db.get_connection(WORKSPACE)
    try:
        node = pc_db.get_node_by_fqn(conn, fqn)
        if not node:
            return {"error": f"Symbol not found: {fqn}"}
        visited = set()
        queue = [(node, 0)]
        impact_nodes = {node["id"]: node}
        total_seen = 1   # 발견된 모든 후보 노드 수 (limit 초과 포함)
        truncated = False
        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_depth or curr["id"] in visited:
                continue
            visited.add(curr["id"])
            neighbors = []
            if direction in ["callers", "both"]:
                neighbors.extend(pc_db.get_callers(conn, curr["id"]))
            if direction in ["callees", "both"]:
                neighbors.extend(pc_db.get_callees(conn, curr["id"]))
            for nb in neighbors:
                if nb["id"] in impact_nodes:
                    continue
                total_seen += 1
                if len(impact_nodes) >= max_nodes:
                    truncated = True
                    continue
                impact_nodes[nb["id"]] = nb
                queue.append((nb, depth + 1))
        returned = [n["fqn"] for n in impact_nodes.values()]
        return {
            "fqn": fqn,
            "impact_nodes": returned,
            "truncated": truncated,
            "limit": max_nodes,
            "returned_count": len(returned),
            "total_seen": total_seen,
        }
    finally:
        conn.close()

def call_pc_logic_flow(args):
    from_fqn = args["from_fqn"]
    to_fqn = args["to_fqn"]
    max_depth = args.get("max_depth", 6)
    max_nodes = args.get("max_nodes", 200)
    conn = pc_db.get_connection(WORKSPACE)
    try:
        start_node = pc_db.get_node_by_fqn(conn, from_fqn)
        end_node = pc_db.get_node_by_fqn(conn, to_fqn)
        if not start_node or not end_node:
            return {"error": "Start or end symbol not found."}
        queue = [[start_node["id"]]]
        visited = set()
        total_seen = 1
        truncated = False
        while queue:
            path = queue.pop(0)
            curr = path[-1]
            if curr == end_node["id"]:
                path_nodes = [pc_db.get_node_by_id(conn, pid) for pid in path]
                returned = [n["fqn"] for n in path_nodes]
                return {
                    "path": returned,
                    "truncated": False,
                    "limit": max_nodes,
                    "returned_count": len(returned),
                    "total_seen": total_seen,
                }
            if len(path) - 1 >= max_depth:
                truncated = True
                continue
            if curr in visited:
                continue
            visited.add(curr)
            if len(visited) >= max_nodes:
                truncated = True
                continue
            callees = pc_db.get_callees(conn, curr)
            for callee in callees:
                total_seen += 1
                queue.append(path + [callee["id"]])
        return {
            "path": [],
            "truncated": truncated,
            "limit": max_nodes,
            "returned_count": 0,
            "total_seen": total_seen,
        }
    finally:
        conn.close()

def call_pc_git_log(args):
    try:
        history = pc_git_mod.get_file_history(WORKSPACE, args["file_path"], args.get("limit", 5))
        return history
    except Exception as e:
        return {"error": str(e)}

def call_pc_capsule(args):
    """pc_capsule 통합 진입점. auto_chain=true 시 기존 pc_auto_explore 부수효과 보존.

    부수효과 (auto_chain=true 한정):
      1. capsule 길이 < 1500 chars 시 impact_graph + memory 자동 체이닝
      2. save_observation에 'Auto-explored: <query>' 기록
    auto_chain=false (기본) 시: 단순 capsule 생성 + chars/tokens 메타만.
    """
    query = args["query"]
    auto_chain = args.get("auto_chain", False)
    token_budget = args.get("token_budget", 4000)

    capsule_str = pc_capsule_mod.generate_context_capsule(WORKSPACE, query, token_budget=token_budget)
    chars = len(capsule_str)
    result = {
        "capsule": capsule_str,
        "chars_used": chars,
        "tokens_estimated": chars // 4,
        "token_budget": token_budget,
    }

    if not auto_chain:
        return result

    # auto_chain=true 부수효과 — pc_auto_explore에서 인라인화
    if chars < 1500:
        result["reasoning"] = f"Generated capsule was relatively short ({chars} chars). Autonomously chaining impact graph and memories..."
        conn = pc_db.get_connection(WORKSPACE)
        try:
            first_match = pc_db.search_nodes_fts(conn, query, limit=1)
            if first_match:
                impact = call_pc_impact_graph({"fqn": first_match[0]["fqn"], "direction": "both", "max_depth": 2})
                result["chained_impact"] = impact.get("impact_nodes", [])[:10]
        finally:
            conn.close()

        if hasattr(pc_mem_mod, "search_memory"):
            mem = pc_mem_mod.search_memory(WORKSPACE, query, limit=3)
            result["chained_memories"] = mem
    else:
        result["reasoning"] = f"Generated capsule is robust ({chars} chars). No further chaining required."

    try:
        pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "insight", f"Auto-explored: {query}", [])
    except Exception:
        pass  # observation 기록 실패가 capsule 응답을 차단해서는 안 됨

    return result


def call_pc_run_pipeline(args):
    query = args["query"]
    limit = args.get("limit", 5)
    try:
        # 1. 통합 교차 검색 수행 (limit + 1로 truncated 추정)
        probe_limit = limit + 1
        unified_full = unified_pipeline_search(WORKSPACE, query, limit=probe_limit, ve_module=ve)
        truncated = len(unified_full) > limit
        unified = unified_full[:limit]
        total_seen = len(unified_full)

        # 2. 코드 도메인 1위 항목 FQN 추출 및 Impact Graph 스킵 처리
        code_results = [r for r in unified if r["domain"] == "code"]
        impact = []
        if code_results:
            fqn = code_results[0].get("key")
            if fqn:
                impact_res = call_pc_impact_graph({"fqn": fqn, "direction": "both", "max_depth": 2})
                impact = impact_res.get("impact_nodes", [])[:10]

        # 3. 보완용 상세 코드 캡슐 생성 (Option B)
        capsule = pc_capsule_mod.generate_context_capsule(WORKSPACE, query)

        return {
            "unified_context": unified,
            "capsule": capsule,
            "impact_summary": impact,
            "truncated": truncated,
            "limit": limit,
            "returned_count": len(unified),
            "total_seen": total_seen,
        }
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

TOOLS = [
    {"name": "pc_reindex", "description": "⚠️ DESTRUCTIVE — 인덱스 전체 재구성. 일상 워크플로에서는 watcher 기반 증분 인덱싱이 자동 동작하므로 호출 불필요. 파서 수정·DB 오염·스키마 마이그레이션 같은 명시적 사유가 있을 때만 사용. force=true는 file_cache 전체 무효화 + 모든 파일 재파싱·재임베딩(GPU 비용) 발생.", "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean", "description": "⚠️ destructive. 호출 시 사유(파서 수정/DB 오염 등)를 명시할 것"}}}},
    {"name": "pc_index_status", "description": "인덱스 상태", "inputSchema": {"type": "object"}},
    {"name": "pc_index_roots_list", "description": "현재 인덱싱 루트 설정 조회", "inputSchema": {"type": "object"}},
    {"name": "pc_index_roots_add", "description": "settings.local.yaml에 인덱싱 루트 추가. 기본 dry_run=true로 스캔 수만 계산.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "워크스페이스 기준 상대 경로 또는 워크스페이스 내부 절대 경로"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["path"]}},
    {"name": "pc_index_roots_remove", "description": "settings.local.yaml의 인덱싱 루트 제거. 기본 dry_run=true로 스캔 수만 계산.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "제거할 인덱싱 루트"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["path"]}},
    {"name": "pc_capsule", "description": "1순위 검색. token_budget는 chars/4 추정 기반(정확한 토크나이저 아님). auto_chain=true 시 짧은 capsule 감지 후 impact_graph+memory 자동 체이닝 + observation 기록 (구 pc_auto_explore 흡수). 응답에 chars_used/tokens_estimated 포함.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "token_budget": {"type": "integer", "description": "토큰 예산 (approximate via chars/4)", "default": 4000}, "auto_chain": {"type": "boolean", "description": "짧은 capsule 시 자동 체이닝 활성화", "default": False}}, "required": ["query"]}},
    {"name": "pc_skeleton", "description": "파일 스켈레톤 출력.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "detail": {"type": "string", "description": "상세 수준", "enum": ["minimal", "standard", "detailed"], "default": "standard"}}, "required": ["file_path"]}},
    {"name": "pc_impact_graph", "description": "영향 범위 추적. 응답에 truncated/limit/returned_count/total_seen 포함.", "inputSchema": {"type": "object", "properties": {"fqn": {"type": "string", "description": "함수/클래스의 FQN"}, "direction": {"type": "string", "description": "추적 방향", "enum": ["callers", "callees", "both"], "default": "both"}, "max_depth": {"type": "integer", "description": "최대 깊이", "default": 2}, "max_nodes": {"type": "integer", "description": "최대 반환 노드 수", "default": 50}}, "required": ["fqn"]}},
    {"name": "pc_logic_flow", "description": "두 기능 간 실행 경로 탐색. 응답에 truncated/limit/returned_count/total_seen 포함.", "inputSchema": {"type": "object", "properties": {"from_fqn": {"type": "string", "description": "시작 지점 FQN"}, "to_fqn": {"type": "string", "description": "종료 지점 FQN"}, "max_depth": {"type": "integer", "description": "경로 최대 깊이", "default": 6}, "max_nodes": {"type": "integer", "description": "탐색 최대 노드 수", "default": 200}}, "required": ["from_fqn", "to_fqn"]}},
    {"name": "pc_git_log", "description": "특정 파일의 상세 Git 수정 이력 조회.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "limit": {"type": "integer", "description": "최대 로그 수", "default": 5}}, "required": ["file_path"]}},
    {"name": "pc_run_pipeline", "description": "캡슐+임팩트+메모리 통합 검색 (고급 종합 탐색 진입점). 코드+그래프+메모리 종합 맥락이 필요한 경우 사용. 응답에 truncated/limit/returned_count/total_seen 포함.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "통합 검색 쿼리"}, "limit": {"type": "integer", "description": "unified_context 항목 수 제한", "default": 5}}, "required": ["query"]}},
    {"name": "pc_auto_context", "description": "세션 시작 시 최신 결정사항과 인기 지식을 요약하여 제공 (맥락 복원).", "inputSchema": {"type": "object", "properties": {"token_budget": {"type": "integer", "description": "토큰 예산", "default": 2000}}}},
    {"name": "pc_read_with_hash", "description": "해시 포함 읽기", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
    {"name": "pc_strict_replace", "description": "정밀 편집", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_content": {"type": "string"}, "new_content": {"type": "string"}}, "required": ["file_path", "old_content", "new_content"]}},
    {"name": "pc_create_contract", "description": "계약 생성", "inputSchema": {"type": "object", "properties": {"lane_id": {"type": "string"}, "task_name": {"type": "string"}, "instructions": {"type": "string"}}, "required": ["lane_id", "task_name", "instructions"]}},
    {"name": "pc_todo_manager", "description": "Todo 관리", "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "description": "add | check | clear"}, "task": {"type": "string", "description": "add 시 등록할 태스크 내용"}, "task_id": {"type": "string", "description": "check 시 완료 표시할 태스크 ID"}}, "required": ["action"]}},
    {"name": "pc_session_sync", "description": "작업 종료 시 Git 상태와 변경 파일을 스캔하여 세션 메모리를 자동 동기화합니다.", "inputSchema": {"type": "object", "properties": {"task_desc": {"type": "string", "description": "작업 요약"}}, "required": ["task_desc"]}},
    {"name": "pc_memory_write", "description": "지식 저장 및 마크다운 승격(decisions/patterns.md)", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}}, "required": ["key", "category", "content"]}},
    {"name": "pc_memory_consolidate", "description": "파편화된 과거 지식을 하나의 새로운 규칙으로 병합. dry_run=true 기본 — 후보만 반환(would_delete/would_write/executed=false). 실행하려면 dry_run=false 명시. 자동 트리거 금지.", "inputSchema": {"type": "object", "properties": {"new_key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "old_keys": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}, "dry_run": {"type": "boolean", "description": "기본 true. false 명시 시에만 실제 삭제·병합 수행", "default": True}}, "required": ["new_key", "category", "content", "old_keys"]}},
    {"name": "pc_memory_read", "description": "지식 조회", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "pc_save_observation", "description": "인사이트 저장", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "pc_memory_search_knowledge", "description": "영구 지식, 규칙 및 스킬 하이브리드 검색", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}}, "required": ["query"]}},
    {"name": "pc_run_background_task", "description": "백그라운드 태스크 실행", "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "lane_id": {"type": "string"}, "task_name": {"type": "string"}}, "required": ["command", "lane_id", "task_name"]}}
]

def handle_request(req):
    m, p, rid = req.get("method"), req.get("params", {}), req.get("id")
    if m == "initialize": return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "Cortex-Hooks", "version": "3.8.0"}}}
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
            elif n == "pc_index_roots_list": r = call_pc_index_roots_list(a)
            elif n == "pc_index_roots_add": r = call_pc_index_roots_add(a)
            elif n == "pc_index_roots_remove": r = call_pc_index_roots_remove(a)
            elif n == "pc_capsule": r = call_pc_capsule(a)
            elif n == "pc_skeleton": r = pc_skeleton_mod.generate_skeleton(WORKSPACE, a["file_path"], a.get("detail", "standard"))
            elif n == "pc_impact_graph": r = call_pc_impact_graph(a)
            elif n == "pc_logic_flow": r = call_pc_logic_flow(a)
            elif n == "pc_git_log": r = call_pc_git_log(a)
            elif n == "pc_run_pipeline": r = call_pc_run_pipeline(a)
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
            
            if isinstance(r, (dict, list)):
                final_res = json.dumps(r, ensure_ascii=False, indent=2)
            else:
                final_res = str(r)
            if hook_msg:
                final_res = f"{hook_msg}\n{final_res}"
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": final_res}]}}
        except Exception as e: return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}]}}
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
