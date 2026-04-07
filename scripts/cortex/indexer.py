"""
Cortex 인덱싱 엔진 (v2.2)
파일 스캔 → 지능형 필터링 → 파서 호출 → DB 저장 → 벡터 임베딩 → 증분 인덱싱
"""
import os
import sys
import time
import hashlib
import fnmatch

# 패키지 내부 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex import db
from cortex.parsers.python_parser import parse_python_file
from cortex.parsers.java_parser import parse_java_file
from cortex.parsers.typescript_parser import parse_typescript_file
from cortex.parsers.markdown_parser import parse_markdown_file

# ==============================================================================
# 설정 및 지원 확장자
# ==============================================================================

SUPPORTED_EXTENSIONS = {
    ".py": ("python", parse_python_file),
    ".java": ("java", parse_java_file),
    ".ts": ("typescript", parse_typescript_file),
    ".tsx": ("typescript", parse_typescript_file),
    ".js": ("javascript", parse_typescript_file),
    ".jsx": ("javascript", parse_typescript_file),
    ".md": ("markdown", parse_markdown_file),
}

DEFAULT_IGNORES = [
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", "build", ".gradle", ".idea", ".vscode", ".vexp",
    ".cortex", "target", ".next", "*.min.js", "*.min.css",
    "*.pyc", "*.class", "skills", "skills/**",
]

# ==============================================================================
# 파일 필터링 및 유틸리티
# ==============================================================================

def strip_frontmatter(content: str) -> str:
    """YAML Frontmatter (--- ... ---) 제거"""
    import re
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

def load_gitignore(workspace: str) -> list:
    """프로젝트의 .gitignore 패턴 로드"""
    patterns = list(DEFAULT_IGNORES)
    gitignore_path = os.path.join(workspace, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line.strip("/"))
        except Exception:
            pass
    return patterns


def should_ignore(path: str, ignore_patterns: list, workspace: str) -> bool:
    """파일/디렉토리가 무시 대상인지 확인"""
    rel = os.path.relpath(path, workspace)
    parts = rel.split(os.sep)
    for part in parts:
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def load_settings(workspace: str) -> dict:
    """.agents/settings.yaml 파일 로드"""
    settings_path = os.path.join(workspace, ".agents", "settings.yaml")
    if os.path.exists(settings_path):
        try:
            import yaml
            with open(settings_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


def should_include(path: str, workspace: str, settings: dict) -> bool:
    """파일이 인덱싱 범위에 포함되는지 확인 (Whitelist 우선)"""
    rules = settings.get("indexing_rules", {})
    rel = os.path.relpath(path, workspace)
    
    # 1. 화이트리스트 파일 체크
    whitelist = rules.get("config_whitelist", [])
    for pattern in whitelist:
        if fnmatch.fnmatch(os.path.basename(rel), pattern) or fnmatch.fnmatch(rel, pattern):
            return True
            
    # 2. 포함 경로 체크
    includes = rules.get("include_paths", ["**/src/**", "**/*.py"])
    for pattern in includes:
        if fnmatch.fnmatch(rel, pattern):
            return True
            
    # 3. 모듈별 경로 체크
    modules = rules.get("modules", {})
    for mod_name, mod_paths in modules.items():
        for m_path in mod_paths:
            if rel.startswith(m_path) or fnmatch.fnmatch(rel, m_path):
                return True
                
    return False


def get_module_name(rel_path: str, settings: dict) -> str:
    """경로 기반 모듈명 판단"""
    modules = settings.get("indexing_rules", {}).get("modules", {})
    for mod_name, mod_paths in modules.items():
        for m_path in mod_paths:
            if f"{m_path}{os.sep}" in f"{rel_path}{os.sep}" or rel_path.endswith(m_path):
                return mod_name
    parts = rel_path.split(os.sep)
    return parts[0] if len(parts) > 1 else "root"


def compute_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()

# ==============================================================================
# 핵심 인덱싱 로직
# ==============================================================================

def index_file(workspace: str, rel_path: str, conn=None, vectorize: bool = True):
    """단일 파일에 대한 정밀 인덱싱 및 임베딩.

    Args:
        vectorize: True(기본) = 즉시 벡터 임베딩까지 수행 (On-Save 단일 파일용).
                   False = 파싱/DB 저장만 수행하고 vector_items를 반환값에 포함
                           (index_workspace의 배치 모드에서 사용).
    """
    full_path = os.path.join(workspace, rel_path)
    if not os.path.exists(full_path):
        return {"error": "File not found"}

    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception as e:
        return {"error": str(e)}

    settings = load_settings(workspace)
    workspace_id = hashlib.md5(workspace.encode()).hexdigest()[:8]
    ext = os.path.splitext(rel_path)[1]
    mod_name = get_module_name(rel_path, settings)
    _, parser_func = SUPPORTED_EXTENSIONS.get(ext, (None, None))
    
    if not parser_func:
        return {"status": "skipped", "reason": "unsupported extension"}

    close_conn = False
    if conn is None:
        conn = db.get_connection(workspace)
        close_conn = True

    try:
        result = parser_func(rel_path, source)
        clean_source = strip_frontmatter(source) if rel_path.startswith(".agents/") else source
        
        # 기존 노드/엣지 삭제
        old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (rel_path,)).fetchall()
        old_ids = [r[0] for r in old_nodes]
        if old_ids:
            chunk_size = 900
            for i in range(0, len(old_ids), chunk_size):
                chunk = old_ids[i:i + chunk_size]
                ph = ",".join("?" * len(chunk))
                conn.execute(f"DELETE FROM edges WHERE source_id IN ({ph})", chunk)
                conn.execute(f"DELETE FROM edges WHERE target_id IN ({ph})", chunk)
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))

        # 신규 노드 저장
        nodes_data = []
        vector_items = []
        cat = "SKILL" if "skills/" in rel_path else ("RULE" if rel_path.startswith(".agents/") else "SOURCE")
        
        for node in result["nodes"]:
            nodes_data.append((
                node["id"], node["type"], node["name"], node["fqn"],
                node["file_path"], node["start_line"], node["end_line"],
                node.get("signature"), node.get("return_type"), node.get("docstring"),
                node.get("is_exported", 1), node.get("is_async", 0), node.get("is_test", 0),
                node["raw_body"], node.get("skeleton_standard"),
                node.get("skeleton_minimal"), node["language"],
                mod_name, workspace_id, cat
            ))
            
            vec_text = f"{node['type']} {node['fqn']}\n"
            if node.get('signature'): vec_text += f"Sig: {node['signature']}\n"
            if cat == "RULE":
                vec_text += clean_source[:1200]
            else:
                vec_text += node['raw_body'][:1200]
            
            vector_items.append({
                "id": node["id"], "text": vec_text,
                "meta": {"module": mod_name, "file": rel_path, "type": node["type"], "category": cat}
            })

        if nodes_data:
            conn.executemany("""
                INSERT OR REPLACE INTO nodes
                (id, type, name, fqn, file_path, start_line, end_line,
                 signature, return_type, docstring, is_exported, is_async,
                 is_test, raw_body, skeleton_standard, skeleton_minimal, language,
                 module, workspace_id, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, nodes_data)

        if edges_data := [(e["source_id"], e["target_id"], e.get("type", "CALLS")) for e in result["edges"]]:
            conn.executemany("INSERT OR IGNORE INTO edges (source_id, target_id, type) VALUES (?, ?, ?)", edges_data)

        conn.execute("INSERT OR REPLACE INTO file_cache (file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)",
                     (rel_path, compute_hash(source), int(time.time()), workspace_id))
        
        if vectorize and vector_items:
            from cortex import vector_engine as ve
            ve.index_texts(workspace, vector_items, use_gpu=False)
            ve._release_gpu()  # On-Save 단일 파일 모드에서는 즉시 해제

        conn.commit()

        # 인덱싱 성공 시 history/inbox.md 자동 갱신
        try:
            from cortex.extract_inbox import extract_to_inbox
            extract_to_inbox()
        except Exception as e:
            import sys
            sys.stderr.write(f"[indexer] Failed to sync inbox.md: {e}\n")

        result = {"status": "success", "nodes": len(nodes_data)}
        if not vectorize:
            # 배치 모드: 호출자가 일괄 처리하도록 vector_items 반환
            result["_vector_items"] = vector_items
        return result
    finally:
        if close_conn:
            conn.close()


def scan_files(workspace: str) -> list:
    """지능형 필터링을 적용하여 인덱싱할 파일 목록 확보"""
    settings = load_settings(workspace)
    ignore_patterns = load_gitignore(workspace)
    
    # [배포 대응] .agents/settings.yaml의 exclude_paths를 ignore_patterns에 추가
    rules = settings.get("indexing_rules", {})
    extra_excludes = rules.get("exclude_paths", [])
    if extra_excludes:
        ignore_patterns.extend([p.strip("/") for p in extra_excludes if p.strip()])
    
    files = []
    
    # 1. 기본 소스 코드 스캔
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns, workspace)]
        for fname in filenames:
            full_path = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1]
            if ext in SUPPORTED_EXTENSIONS:
                if not should_ignore(full_path, ignore_patterns, workspace):
                    if should_include(full_path, workspace, settings):
                        files.append(os.path.relpath(full_path, workspace))
                        
    # 2. .agents 내부 규칙 및 프로토콜 강제 포함
    agent_docs = [".agents/rules", ".agents/protocols"]
    for doc_dir in agent_docs:
        abs_doc_dir = os.path.join(workspace, doc_dir)
        if os.path.exists(abs_doc_dir):
            for root, _, filenames in os.walk(abs_doc_dir):
                for fname in filenames:
                    if fname.endswith(".md"):
                        files.append(os.path.relpath(os.path.join(root, fname), workspace))
                        
    return sorted(list(set(files)))


def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 하이브리드 인덱싱.

    최적화:
    - 파싱/DB 저장은 파일별로 수행하되, 벡터 임베딩은 전체 완료 후 1회 배치 처리.
    - 모델 로드 1회 / FAISS 읽기·쓰기 1회 / GPU 해제 1회.
    """
    files = scan_files(workspace)
    conn = db.get_connection(workspace)
    db.init_schema(conn)

    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}

    # N+1 최적화: file_cache 일괄 로드
    cache_dict = {}
    if not force:
        cached_rows = conn.execute("SELECT file_path, hash FROM file_cache").fetchall()
        cache_dict = {row[0]: row[1] for row in cached_rows}

    all_vector_items = []  # 전체 vector_items 수집 (배치 임베딩용)

    for rel_path in files:
        full_path = os.path.join(workspace, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except Exception:
            stats["errors"] += 1
            continue

        file_hash = compute_hash(source)
        if not force:
            cached_hash = cache_dict.get(rel_path)
            if cached_hash == file_hash:
                stats["skipped"] += 1
                continue

        # vectorize=False: DB 저장만 수행, vector_items는 여기서 수집
        res = index_file(workspace, rel_path, conn=conn, vectorize=False)
        if "error" in res:
            stats["errors"] += 1
        else:
            stats["indexed"] += 1
            all_vector_items.extend(res.get("_vector_items", []))

    # 전체 파일 파싱 완료 후 벡터 임베딩 1회 배치 처리
    if all_vector_items:
        from cortex import vector_engine as ve
        ve.index_texts(workspace, all_vector_items, use_gpu=True)
        ve._release_gpu()  # 배치 완료 후 GPU 해제

    conn.close()
    return stats


if __name__ == "__main__":
    import json
    import argparse
    
    parser = argparse.ArgumentParser(description="Cortex Indexer")
    parser.add_argument("workspace", help="Path to workspace")
    parser.add_argument("--file", help="Specific file to index (relative path)")
    parser.add_argument("--force", action="store_true", help="Force re-indexing")
    
    args = parser.parse_args()
    
    if args.file:
        # 단일 파일 모드
        result = index_file(args.workspace, args.file)
        print(json.dumps(result, indent=2))
    else:
        # 전체 워크스페이스 모드
        stats = index_workspace(args.workspace, force=args.force)
        print(json.dumps(stats, indent=2))
