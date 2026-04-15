#!/usr/bin/env python3
"""
Cortex 전용 MCP 서버 (v3: Git & Memory & Pipeline 지원)
로컬 컨텍스트 엔진 MCP 서버입니다.
"""
import sys
import json
import os
import uuid
import subprocess
import re
import yaml
import importlib
from pathlib import Path

# 경로 설정 (현재 파일: .agents/scripts/cortex_mcp.py)
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from cortex import vector_engine as _ve
    _VE_AVAILABLE = True
except Exception:
    _ve = None
    _VE_AVAILABLE = False


def _find_real_workspace(start_path):
    """상위로 올라가며 .git 또는 프로젝트 시그니처 폴더를 찾아 실제 워크스페이스를 반환"""
    curr = start_path.resolve()
    # 1단계: 만약 현재 경로가 .agents 내부라면 무조건 부모로 올라가서 시작
    parts = curr.parts
    if ".agents" in parts:
        idx = parts.index(".agents")
        curr = Path(*parts[:idx])
        return str(curr)

    # 2단계: 일반적인 탐색 (최대 5레벨)
    for _ in range(5):
        # .git이 있거나 .agents 설정 폴더가 있는 곳을 루트로 간주
        if (curr / ".git").exists() or (curr / ".agents").exists():
            # 단, 현재 폴더 자체가 .agents이면 안 됨 (부모가 루트여야 함)
            if curr.name == ".agents":
                curr = curr.parent
                continue
            return str(curr)
        if curr.parent == curr: break
        curr = curr.parent
    
    # Fallback: scripts/cortex_mcp.py 기준 두 단계 위 (.agents의 부모)
    return str(SCRIPTS_DIR.parent.parent)

WORKSPACE = _find_real_workspace(SCRIPTS_DIR)

# 유틸리티: persistent_memory 모듈에서 임포트
def _append_markdown_with_archive(target_filename, content):
    from cortex.persistent_memory import append_markdown_with_archive
    append_markdown_with_archive(WORKSPACE, target_filename, content)

# Cortex 모듈 임포트
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from cortex import db as pc_db
    import cortex.indexer as pc_indexer
    from cortex import capsule as pc_capsule_mod
    from cortex import impact as pc_impact_mod
    from cortex import skeleton as pc_skeleton_mod
    from cortex import git_analyzer as pc_git_mod
    from cortex import memory as pc_mem_mod
    from cortex.persistent_memory import PersistentMemoryManager
    from cortex.skill_manager import SkillManager
    except ImportError:
    # 패키지 구조에 따른 폴백
    import cortex.db as pc_db # type: ignore
    import cortex.indexer as pc_indexer # type: ignore
    import cortex.capsule as pc_capsule_mod # type: ignore
    import cortex.impact as pc_impact_mod # type: ignore
    import cortex.skeleton as pc_skeleton_mod # type: ignore
    import cortex.git_analyzer as pc_git_mod # type: ignore
    import cortex.memory as pc_mem_mod # type: ignore
    from cortex.persistent_memory import PersistentMemoryManager # type: ignore
    from cortex.skill_manager import SkillManager # type: ignore
    
# ------------------------------------------------------------------------------
# Cortex Tools Logic
# ------------------------------------------------------------------------------
SESSION_ID = str(uuid.uuid4())[:8]

# 전역 인스턴스 (지연 초기화 방식으로 변경)
_storage = None
_skills = None

def get_storage():
    global _storage
    if _storage is None:
        sys.stderr.write("[cortex] Initializing PersistentMemoryManager...\n")
        _storage = PersistentMemoryManager(WORKSPACE)
    return _storage

def get_skills():
    global _skills
    if _skills is None:
        sys.stderr.write("[cortex] Initializing SkillManager...\n")
        _skills = SkillManager(WORKSPACE)
    return _skills

def _auto_sync():
    """도구 실행 전 자동 증분 인덱싱 (On-run Sync)"""
    try:
        importlib.reload(pc_indexer)
        pc_indexer.index_workspace(WORKSPACE, force=False)
    except Exception:
        pass

def pc_reindex(force=False):
    importlib.reload(pc_indexer)
    stats = pc_indexer.index_workspace(WORKSPACE, force=force)
    conn = pc_db.get_connection(WORKSPACE)
    db_stats = pc_db.get_stats(conn)
    conn.close()
    return json.dumps({"indexing": stats, "database": db_stats, "session": SESSION_ID}, ensure_ascii=False)

def pc_index_status():
    try:
        conn = pc_db.get_connection(WORKSPACE)
        stats = pc_db.get_stats(conn)
        last_indexed = conn.execute("SELECT value FROM meta WHERE key='last_indexed_at'").fetchone()
        stats["last_indexed_at"] = last_indexed[0] if last_indexed else "never"
        conn.close()
        return json.dumps(stats, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_capsule(query, context=None, category=None):
    try:
        _auto_sync()
        search_query = f"{query} {context}" if context else query
        capsule_text = pc_capsule_mod.generate_context_capsule(WORKSPACE, search_query, category=category)
        pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "insight", f"Capsule search for: {search_query}", [])
        return json.dumps({"capsule": capsule_text}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_logic_flow(from_fqn, to_fqn):
    conn = pc_db.get_connection(WORKSPACE)
    flow_data = pc_impact_mod.find_logic_flow(conn, from_fqn, to_fqn)
    conn.close()
    return json.dumps(flow_data, ensure_ascii=False)



def pc_save_observation(content, obs_type="insight", file_paths=None):
    from cortex.db import to_rel_path
    from cortex.extract_inbox import extract_to_inbox
    if file_paths:
        file_paths = [to_rel_path(p, WORKSPACE) for p in file_paths]
    success = pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, obs_type, content, file_paths)
    try:
        extract_to_inbox() # 즉시 파일로 추출하여 가시화
    except Exception:
        pass
    return json.dumps({"success": success, "session_id": SESSION_ID})

def pc_search_memory(query, limit=10):
    results = pc_mem_mod.search_memory(WORKSPACE, query, limit)
    return json.dumps(results, ensure_ascii=False)

# --- 영구 지식 / 스킬 도구 ---

def pc_memory_write(key, category, content, tags=None, relationships=None):
    """ADR, 아키텍처 결정, 프로토콜 등 영구 지식 저장"""
    try:
        data = {
            "key": key,
            "category": category,
            "content": content,
            "tags": tags or [],
            "relationships": relationships or {},
        }
        ok = get_storage().write("default", data)
        
        # [NEW] 마크다운 자동 승격 (DB와 동기화 + 아카이브 관리)
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
                    
        return json.dumps({"success": ok, "key": key, "auto_promoted_to": target_file}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_memory_consolidate(new_key, category, content, old_keys, tags=None, relationships=None):
    """여러 과거 기록 파편들(old_keys)을 지우고, 하나의 새로운 규칙(new_key)으로 병합 승격합니다."""
    try:
        st = get_storage()
        
        # 1. 과거 찌꺼기 정리
        deleted_count = st.delete_many("default", old_keys)
        
        # 2. 새 병합 패턴 생성
        data = {
            "key": new_key,
            "category": category,
            "content": content,
            "tags": tags or [],
            "relationships": relationships or {},
        }
        ok = st.write("default", data)
        
        # 3. 마크다운 파일 정리 (inbox.md 임시 공간에서 지워졌음을 안내, 그리고 패턴 문서 추가)
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
        
        return json.dumps({"success": ok, "consolidated_key": new_key, "deleted_old_fragments": deleted_count, "auto_promoted_to": target_file}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_memory_read(key):
    """key로 주요 지식 조회"""
    try:
        return json.dumps(get_storage().read("default", key), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_memory_search_knowledge(query, context=None, category=None, limit=10):
    """영구 지식 및 전문가 스킬 검색 → persistent_memory.search_knowledge()에 위임"""
    try:
        search_query = f"{query} {context}" if context else query
        ve = _ve if _VE_AVAILABLE else None
        results = get_storage().search_knowledge(search_query, category, limit, ve_module=ve)
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_memory_sync_skills():
    """스킬 디렉터리(skills/**/*.md)를 탐색하여 memories DB에 인덱싱"""
    try:
        result = get_skills().sync_skills("default")
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_memory_stats():
    """영구 지식 저장소 통계"""
    try:
        return json.dumps(get_storage().get_stats("default"), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_git_log(file_path, limit=5):
    try:
        history = pc_git_mod.get_file_history(WORKSPACE, file_path, limit)
        return json.dumps(history, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_skeleton(file_path, detail="standard"):
    try:
        _auto_sync()
        skeleton = pc_skeleton_mod.generate_skeleton(WORKSPACE, file_path, detail)
        return json.dumps({"skeleton": skeleton}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_run_pipeline(query, context=None, category=None):
    """최종 통합 파이프라인: Capsule + Impact + Memory 결합"""
    try:
        _auto_sync()
        search_query = f"{query} {context}" if context else query
        # 1. 캡슐 생성
        capsule = pc_capsule_mod.generate_context_capsule(WORKSPACE, search_query, category=category)
        
        # 2. 관련 기호 임팩트 분석 (가장 첫번째 Pivot 기준)
        conn = pc_db.get_connection(WORKSPACE)
        first_match = pc_db.search_nodes_fts(conn, search_query, category=category, limit=1)
        impact = {}
        if first_match:
            impact = pc_impact_mod.get_impact_tree(conn, first_match[0]["id"], max_depth=2)
        conn.close()
        
        # 3. 과거 메모리 검색
        mem = pc_mem_mod.search_memory(WORKSPACE, search_query, limit=3)
        
        return json.dumps({
            "capsule": capsule,
            "impact_summary": [n["fqn"] for n in impact.get("nodes", {}).values()][:10],
            "related_memories": mem
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_auto_explore(query, context=None, category=None):
    """조건부 지능형 다단계 탐색기 (파이썬 내부 자율 추론 엔진)"""
    try:
        _auto_sync()
        search_query = f"{query} {context}" if context else query
        capsule = pc_capsule_mod.generate_context_capsule(WORKSPACE, search_query, category=category)
        result = {"capsule": capsule}
        
        if len(capsule) < 1500:
            result["reasoning"] = f"Generated capsule was relatively short ({len(capsule)} chars). Autonomously chaining impact graph and memories..."
            conn = pc_db.get_connection(WORKSPACE)
            first_match = pc_db.search_nodes_fts(conn, search_query, category=category, limit=1)
            impact = {}
            if first_match:
                impact = pc_impact_mod.get_impact_tree(conn, first_match[0]["id"], max_depth=2)
            conn.close()
            
            mem = pc_mem_mod.search_memory(WORKSPACE, search_query, limit=3)
            result["chained_impact"] = [n["fqn"] for n in impact.get("nodes", {}).values()][:10]
            result["chained_memories"] = mem
        else:
            result["reasoning"] = f"Generated capsule is robust ({len(capsule)} chars). No further chaining required."
            
        pc_mem_mod.save_observation(WORKSPACE, SESSION_ID, "insight", f"Auto-explored: {search_query}", [])
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def pc_session_sync(task_desc, auto_release_agent=None):
    """자율 관계 추출기를 통한 세션 메모리 자동 동기화 및 자동 락 해제"""
    try:
        branch = "unknown"
        jira_issues = []
        try:
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE).decode().strip()
            match = re.search(r'([A-Z0-9]+-\d+)', branch)
            if match:
                jira_issues.append(match.group(1))
        except Exception:
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
        except Exception:
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
                
        if auto_release_agent:
            try:
                relay_script = os.path.join(SCRIPTS_DIR, "relay.py")
                msg = f"Task: {task_desc[:50]} | Modifies: {len(modified_files)} files"
                subprocess.run([sys.executable, relay_script, "release", auto_release_agent, "", msg], cwd=WORKSPACE)
            except Exception:
                pass
                
        return json.dumps({"success": ok, "key": key, "extracted_relationships": relationships, "markdown_synced": True}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

TOOLS = [
    # === 인덱싱 관리 ===
    {"name": "pc_reindex", "description": "프로젝트 증분 인덱싱 실행.", "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean", "description": "강제 재발행 여부", "default": False}}}},
    {"name": "pc_index_status", "description": "인덱스 통계 조회.", "inputSchema": {"type": "object", "properties": {}}},

    # === 검색 (우선순위: pc_capsule > pc_memory_search_knowledge > pc_run_pipeline) ===
    {"name": "pc_capsule", "description": "[1순위 검색] 소스코드+스킬 통합 검색. 압축된 컨텍스트 반환. 검색 시 가장 먼저 사용.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "검색 쿼리"}, "context": {"type": "string", "description": "현재 작업 중인 파일명이나 기술 키워드 (선택 사항)"}, "category": {"type": "string", "description": "필터링할 카테고리 (SOURCE 또는 SKILL)"}}, "required": ["query"]}},
    {"name": "pc_memory_search_knowledge", "description": "[2순위 검색] 스킬·지식 상세 검색. pc_capsule 부족 시 사용. 200자 요약+점수 반환.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "context": {"type": "string", "description": "현재 작업 중인 파일명이나 기술 키워드 (선택 사항)"}, "category": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]}},
    {"name": "pc_run_pipeline", "description": "캡슐+임팩트+메모리 통합 검색. 복잡한 분석이 필요할 때 3순위로 사용.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "통합 검색 쿼리"}, "context": {"type": "string", "description": "현재 작업 중인 파일명이나 기술 키워드 (선택 사항)"}, "category": {"type": "string", "description": "필터링 카테고리"}}, "required": ["query"]}},
    {"name": "pc_auto_explore", "description": "AI 내재화 자율 탐색기. 캡슐 텍스트 길이를 판별해 필요 시 추가 도구를 스크립트가 알아서 체이닝합니다.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "검색 쿼리"}, "context": {"type": "string", "description": "현재 작업 중인 파일명이나 기술 키워드 (선택 사항)"}, "category": {"type": "string", "description": "필터링 카테고리"}}, "required": ["query"]}},

    # === 코드 구조 분석 === "direction": {"type": "string", "description": "추적 방향", "enum": ["callers", "callees", "both"], "default": "both"}, "max_depth": {"type": "integer", "description": "최대 깊이", "default": 3}}, "required": ["fqn"]}},
    {"name": "pc_logic_flow", "description": "두 기능 간 실행 경로 탐색.", "inputSchema": {"type": "object", "properties": {"from_fqn": {"type": "string", "description": "시작 지점 FQN"}, "to_fqn": {"type": "string", "description": "종료 지점 FQN"}}, "required": ["from_fqn", "to_fqn"]}},
    {"name": "pc_skeleton", "description": "파일 스켈레톤 출력.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "detail": {"type": "string", "description": "상세 수준", "enum": ["minimal", "standard", "detailed"], "default": "standard"}}, "required": ["file_path"]}},

    # === 관찰/이력 (스킬·지식 검색이 아님) ===
    {"name": "pc_save_observation", "description": "작업 중 발견한 중요한 통찰/결정 저장 (기존 agent-memory 대체).", "inputSchema": {"type": "object", "properties": {"content": {"type": "string", "description": "관찰 내용"}, "obs_type": {"type": "string", "description": "관찰 유형", "default": "insight"}, "file_paths": {"type": "array", "items": {"type": "string"}, "description": "관련 파일 경로 목록"}}, "required": ["content"]}},
    {"name": "pc_search_memory", "description": "과거 관찰(observation) 이력만 검색. 스킬·지식 검색은 pc_capsule을 사용.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "검색어"}, "limit": {"type": "integer", "description": "최대 결과 수", "default": 10}}, "required": ["query"]}},
    {"name": "pc_git_log", "description": "특정 파일의 상세 Git 수정 이력 조회.", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string", "description": "파일 경로"}, "limit": {"type": "integer", "description": "최대 로그 수", "default": 5}}, "required": ["file_path"]}},
    {"name": "pc_session_sync", "description": "작업 종료 시 수정 파일 관계를 저장하고, auto_release_agent 제공 시 자동으로 락을 해제합니다.", "inputSchema": {"type": "object", "properties": {"task_desc": {"type": "string", "description": "지금까지 한 작업 요약"}, "auto_release_agent": {"type": "string", "description": "락 해제를 수행할 에이전트명 (예: Antigravity)"}}, "required": ["task_desc"]}},

    # === 영구 지식 / 스킬 관리 ===
    {"name": "pc_memory_write", "description": "ADR, 아키텍처 결정, 프로토콜 등 영구 지식 저장.", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}}, "required": ["key", "category", "content"]}},
    {"name": "pc_memory_consolidate", "description": "과거 임시 기록들(old_keys)을 깔끔하게 지우고 하나의 새로운 지식(new_key)으로 묶어 DB 파편화를 막습니다.", "inputSchema": {"type": "object", "properties": {"new_key": {"type": "string"}, "category": {"type": "string"}, "content": {"type": "string"}, "old_keys": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}, "relationships": {"type": "object"}}, "required": ["new_key", "category", "content", "old_keys"]}},
    {"name": "pc_memory_read", "description": "특정 key의 영구 지식 조회.", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "pc_memory_sync_skills", "description": "스킬 디렉터리(skills/**/*.md)를 탐색하여 memories DB에 인덱싱.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pc_memory_stats", "description": "영구 지식 저장소 통계.", "inputSchema": {"type": "object", "properties": {}}}, "serverInfo": {"name": "Cortex-MCP", "version": "3.1.0"}}}
    
    if method == "tools/list":
        sys.stderr.write(f"[cortex] Listing tools (ID: {rid})...\n")
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        sys.stderr.write(f"[cortex] Calling tool: {name}\n")
        try:
            if name == "pc_reindex": res = pc_reindex(args.get("force", False))
            elif name == "pc_index_status": res = pc_index_status()
            elif name == "pc_capsule": res = pc_capsule(args["query"], context=args.get("context"), category=args.get("category"))
            elif name == "pc_logic_flow": res = pc_logic_flow(args["from_fqn"], args["to_fqn"])
            elif name == "pc_save_observation": res = pc_save_observation(args["content"], args.get("obs_type", "insight"), args.get("file_paths"))
            elif name == "pc_search_memory": res = pc_search_memory(args["query"], args.get("limit", 10))
            elif name == "pc_git_log": res = pc_git_log(args["file_path"], args.get("limit", 5))
            elif name == "pc_run_pipeline": res = pc_run_pipeline(args["query"], context=args.get("context"), category=args.get("category"))
            elif name == "pc_auto_explore": res = pc_auto_explore(args["query"], context=args.get("context"), category=args.get("category"))
            elif name == "pc_session_sync": res = pc_session_sync(args["task_desc"], args.get("auto_release_agent"))
            elif name == "pc_skeleton": res = pc_skeleton(args["file_path"], args.get("detail", "standard"))
            elif name == "pc_memory_write": res = pc_memory_write(args["key"], args["category"], args["content"], args.get("tags"), args.get("relationships"))
            elif name == "pc_memory_consolidate": res = pc_memory_consolidate(args["new_key"], args["category"], args["content"], args["old_keys"], args.get("tags"), args.get("relationships"))
            elif name == "pc_memory_read": res = pc_memory_read(args["key"])
            elif name == "pc_memory_search_knowledge": res = pc_memory_search_knowledge(args["query"], context=args.get("context"), category=args.get("category"), limit=args.get("limit", 10))
            elif name == "pc_memory_sync_skills": res = pc_memory_sync_skills()
            elif name == "pc_memory_stats": res = pc_memory_stats()
            else: return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
            
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(res)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Error: {str(e)}"}]}}
            
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return None

def serve():
    sys.stderr.write("[cortex] MCP Server Starting...\n")
    sys.stderr.flush()
    # stdin에서 한 줄씩 읽기 (readline 사용으로 버퍼링 최소화)
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            res = handle_request(req)
            if res:
                sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error handling request: {str(e)}\n")
            sys.stderr.flush()
            # 예외 발생 시 클라이언트 대기(마비) 상태를 방지하기 위해 에러 응답 반환
            try:
                rid = req.get("id") if 'req' in locals() and isinstance(req, dict) else None
                if rid is not None:
                    err_res = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"Internal Error: {str(e)}"}}
                    sys.stdout.write(json.dumps(err_res, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except Exception:
                pass

if __name__ == "__main__":
    serve()
