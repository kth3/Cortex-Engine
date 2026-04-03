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

def index_file(workspace: str, rel_path: str, conn=None):
    """단일 파일에 대한 정밀 인덱싱 및 임베딩 (On-Save용)"""
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
        
        if vector_items:
            from cortex import vector_engine as ve
            ve.index_texts(workspace, vector_items, use_gpu=True)
            
        conn.commit()
        return {"status": "success", "nodes": len(nodes_data)}
    finally:
        if close_conn:
            conn.close()


def scan_files(workspace: str) -> list:
    """지능형 필터링을 적용하여 인덱싱할 파일 목록 확보"""
    settings = load_settings(workspace)
    ignore_patterns = load_gitignore(workspace)
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
    """전체 워크스페이스 하이브리드 인덱싱"""
    files = scan_files(workspace)
    conn = db.get_connection(workspace)
    db.init_schema(conn)
    
    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}
    
    for rel_path in files:
        # 해시 체크만 수행 (성능 최적화)
        full_path = os.path.join(workspace, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except:
            stats["errors"] += 1
            continue
            
        file_hash = compute_hash(source)
        if not force:
            cached = conn.execute("SELECT hash FROM file_cache WHERE file_path = ?", (rel_path,)).fetchone()
            if cached and cached[0] == file_hash:
                stats["skipped"] += 1
                continue
        
        # 실제 인덱싱은 index_file 함수 재활용
        res = index_file(workspace, rel_path, conn=conn)
        if "error" in res:
            stats["errors"] += 1
        else:
            stats["indexed"] += 1

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
