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
from cortex.search_engine import unified_pipeline_search
from cortex import vector_engine as ve

def _find_real_workspace(start_path):
    # 환경변수 우선 오버라이드 (CI 등 외부 주입 시 사용)
    env_ws = os.environ.get("CORTEX_WORKSPACE")
    if env_ws:
        return str(Path(env_ws).resolve())
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

def _append_markdown_with_archive(target_filename, content):
    import datetime
    import shutil
    md_path = os.path.join(WORKSPACE, ".agents", "history", target_filename)
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = os.path.join(WORKSPACE, ".agents", "history", "archive")
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
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    st = get_storage()
    deleted_count = st.delete_many("default", old_keys)
    data = {
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    ok = st.write("default", data)
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule"]:
        target_file = "patterns.md"
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {new_key} (Consolidated from {len(old_keys)} items)\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {"success": ok, "consolidated_key": new_key, "deleted_old_fragments": deleted_count, "auto_promoted_to": target_file}

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
    yaml_path = os.path.join(WORKSPACE, ".agents", "history", "memory.yaml")
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
    md_path = os.path.join(WORKSPACE, ".agents", "history", target_filename)
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = os.path.join(WORKSPACE, ".agents", "history", "archive")
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
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    st = get_storage()
    deleted_count = st.delete_many("default", old_keys)
    data = {
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    ok = st.write("default", data)
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule"]:
        target_file = "patterns.md"
    if target_file and ok:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {new_key} (Consolidated from {len(old_keys)} items)\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(target_file, log_line)
    return {"success": ok, "consolidated_key": new_key, "deleted_old_fragments": deleted_count, "auto_promoted_to": target_file}

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
    yaml_path = os.path.join(WORKSPACE, ".agents", "history", "memory.yaml")
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
    max_depth = args.get("max_depth", 3)
    conn = pc_db.get_connection(WORKSPACE)
    try:
        node = pc_db.get_node_by_fqn(conn, fqn)
        if not node:
            return {"error": f"Symbol not found: {fqn}"}
        visited = set()
        queue = [(node, 0)]
        impact_nodes = {node["id"]: node}
        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_depth or curr["id"] in visited:
                continue
            visited.add(curr["id"])
            if direction in ["callers", "both"]:
                callers = pc_db.get_callers(conn, curr["id"])
                for caller in callers:
                    if caller["id"] not in impact_nodes:
                        impact_nodes[caller["id"]] = caller
                        queue.append((caller, depth + 1))
            if direction in ["callees", "both"]:
                callees = pc_db.get_callees(conn, curr["id"])
                for callee in callees:
                    if callee["id"] not in impact_nodes:
                        impact_nodes[callee["id"]] = callee
                        queue.append((callee, depth + 1))
        return {"fqn": fqn, "impact_nodes": [n["fqn"] for n in impact_nodes.values()]}
    finally:
        conn.close()

def call_pc_logic_flow(args):
    from_fqn = args["from_fqn"]
    to_fqn = args["to_fqn"]
    conn = pc_db.get_connection(WORKSPACE)
    try:
        start_node = pc_db.get_node_by_fqn(conn, from_fqn)
        end_node = pc_db.get_node_by_fqn(conn, to_fqn)
        if not start_node or not end_node:
            return {"error": "Start or end symbol not found."}
        queue = [[start_node["id"]]]
        visited = set()
        while queue:
            path = queue.pop(0)
            curr = path[-1]
            if curr == end_node["id"]:
                path_nodes = [pc_db.get_node_by_id(conn, pid) for pid in path]
                return {"path": [n["fqn"] for n in path_nodes]}
            if curr not in visited:
                visited.add(curr)
                callees = pc_db.get_callees(conn, curr)
                for callee in callees:
                    queue.append(path + [callee["id"]])
        return {"path": []}
    finally:
        conn.close()

def call_pc_git_log(args):
    try:
        history = pc_git_mod.get_file_history(WORKSPACE, args["file_path"], args.get("limit", 5))
        return history
    except Exception as e:
        return {"error": str(e)}

def call_pc_run_pipeline(args):
    query = args["query"]
    try:
        # 1. 통합 교차 검색 수행
        unified = unified_pipeline_search(WORKSPACE, query, limit=5, ve_module=ve)
        
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
            "impact_summary": impact
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

        return {
            "context": "\n".join(sections),
            "totalChars": total_chars,
            "itemCount": len(sections)
        }
    finally:
        conn.close()

def call_pc_auto_explore(args):
    query = args["query"]
    token_budget = args.get("token_budget", 15000)
    # pc_capsule_mod uses token_budget conditionally or might not have it, let's pass securely
    capsule = pc_capsule_mod.generate_context_capsule(WORKSPACE, query)
    result = {"capsule": capsule}
    if len(capsule) < 1500:
        result["reasoning"] = f"Generated capsule was relatively short ({len(capsule)} chars). Autonomously chaining impact graph and memories..."
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
        result["reasoning"] = f"Generated capsule is robust ({len(capsule)} chars). No further chaining required."
    pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "insight", f"Auto-explored: {query}", [])
    return result

# ==============================================================================
# TOOL REGISTRY
# ==============================================================================

TOOLS = [
    {"name": "pc_reindex", "description": "인덱싱 실행", "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean"}}}},
    {"name": "pc_index_status", "description": "인덱스 상태", "inputSchema": {"type": "object"}},
    {"name": "pc_capsule", "description": "1순위 검색", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "pc_skeleton", "description": "파일 스켈레톤 출력.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "detail": {"type": "string", "description": "상세 수준", "enum": ["minimal", "standard", "detailed"], "default": "standard"}}, "required": ["file_path"]}},
    {"name": "pc_auto_explore", "description": "AI 내재화 자율 탐색기. 캡슐 텍스트 길이를 판별해 필요 시 추가 도구를 스크립트가 알아서 체이닝합니다.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "검색 쿼리"}, "token_budget": {"type": "integer", "description": "토큰 제한", "default": 15000}}, "required": ["query"]}},
    {"name": "pc_impact_graph", "description": "영향 범위 추적.", "inputSchema": {"type": "object", "properties": {"fqn": {"type": "string", "description": "함수/클래스의 FQN"}, "direction": {"type": "string", "description": "추적 방향", "enum": ["callers", "callees", "both"], "default": "both"}, "max_depth": {"type": "integer", "description": "최대 깊이", "default": 3}}, "required": ["fqn"]}},
    {"name": "pc_logic_flow", "description": "두 기능 간 실행 경로 탐색.", "inputSchema": {"type": "object", "properties": {"from_fqn": {"type": "string", "description": "시작 지점 FQN"}, "to_fqn": {"type": "string", "description": "종료 지점 FQN"}}, "required": ["from_fqn", "to_fqn"]}},
    {"name": "pc_git_log", "description": "특정 파일의 상세 Git 수정 이력 조회.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "limit": {"type": "integer", "description": "최대 로그 수", "default": 5}}, "required": ["file_path"]}},
    {"name": "pc_run_pipeline", "description": "캡슐+임팩트+메모리 통합 검색.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "통합 검색 쿼리"}}, "required": ["query"]}},
    {"name": "pc_auto_context", "description": "세션 시작 시 최신 결정사항과 인기 지식을 요약하여 제공 (맥락 복원).", "inputSchema": {"type": "object", "properties": {"token_budget": {"type": "integer", "description": "토큰 예산", "default": 2000}}}},
    {"name": "pc_read_with_hash", "description": "해시 포함 읽기", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
    {"name": "pc_strict_replace", "description": "정밀 편집", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_content": {"type": "string"}, "new_content": {"type": "string"}}, "required": ["file_path", "old_content", "new_content"]}},
    {"name": "pc_create_contract", "description": "계약 생성", "inputSchema": {"type": "object", "properties": {"lane_id": {"type": "string"}, "task_name": {"type": "string"}, "instructions": {"type": "string"}}, "required": ["lane_id", "task_name", "instructions"]}},
    {"name": "pc_todo_manager", "description": "Todo 관리", "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}},
    {"name": "pc_session_sync", "description": "작업 종료 시 Git 상태와 변경 파일을 스캔하여 세션 메모리를 자동 동기화합니다.", "inputSchema": {"type": "object", "properties": {"task_desc": {"type": "string", "description": "작업 요약"}}, "required": ["task_desc"]}},
    {"name": "pc_memory_write", "description": "지식 저장 및 마크다운 승격(decisions/patterns.md)", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}}, "required": ["key", "category", "content"]}},
    {"name": "pc_memory_consolidate", "description": "파편화된 과거 지식을 하나의 새로운 규칙으로 병합.", "inputSchema": {"type": "object", "properties": {"new_key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "old_keys": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}}, "required": ["new_key", "category", "content", "old_keys"]}},
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
            elif n == "pc_skeleton": r = pc_skeleton_mod.generate_skeleton(WORKSPACE, a["file_path"], a.get("detail", "standard"))
            elif n == "pc_auto_explore": r = call_pc_auto_explore(a)
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



def serve():
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
