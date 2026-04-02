"""
Cortex 인덱싱 엔진 (v2.1)
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
    ".agents", "*.pyc", "*.class", "skills", "skills/**",
]

# ==============================================================================
# 파일 필터링 및 유틸리티
# ==============================================================================

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
    includes = rules.get("include_paths", ["**/src/**"])
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
            # 상위 폴더에 관계없이 폴더 구조가 포함되어 있으면 매칭
            if f"{m_path}{os.sep}" in f"{rel_path}{os.sep}" or rel_path.endswith(m_path):
                return mod_name
    parts = rel_path.split(os.sep)
    return parts[0] if len(parts) > 1 else "root"


def compute_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()

# ==============================================================================
# 핵심 인덱싱 로직
# ==============================================================================

def scan_files(workspace: str) -> list:
    """지능형 필터링을 적용하여 인덱싱할 파일 목록 확보"""
    settings = load_settings(workspace)
    ignore_patterns = load_gitignore(workspace)
    files = []
    
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns, workspace)]
        for fname in filenames:
            full_path = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1]
            if ext in SUPPORTED_EXTENSIONS:
                if not should_ignore(full_path, ignore_patterns, workspace):
                    if should_include(full_path, workspace, settings):
                        files.append(os.path.relpath(full_path, workspace))
    return sorted(files)


def index_workspace(workspace: str, force: bool = False) -> dict:
    """하이브리드 인덱싱 (SQLite + BGE-M3)"""
    settings = load_settings(workspace)
    workspace_id = hashlib.md5(workspace.encode()).hexdigest()[:8]
    
    conn = db.get_connection(workspace)
    db.init_schema(conn)
    
    from cortex import vector_engine as ve
    vector_items = []
    
    files = scan_files(workspace)
    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}
    
    for rel_path in files:
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
                
        ext = os.path.splitext(rel_path)[1]
        mod_name = get_module_name(rel_path, settings)
        _, parser_func = SUPPORTED_EXTENSIONS.get(ext, (None, None))
        if not parser_func: continue
        
        try:
            result = parser_func(rel_path, source)
        except:
            stats["errors"] += 1
            continue
            
        # 기존 데이터 삭제
        old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (rel_path,)).fetchall()
        old_ids = [r[0] for r in old_nodes]
        if old_ids:
            ph = ",".join("?" * len(old_ids))
            conn.execute(f"DELETE FROM edges WHERE source_id IN ({ph})", old_ids)
            conn.execute(f"DELETE FROM edges WHERE target_id IN ({ph})", old_ids)
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))
            
        # 노드/벡터 저장
        for node in result["nodes"]:
            cat = "SKILL" if "skills/" in rel_path or "skills\\" in rel_path else "SOURCE"
            conn.execute("""
                INSERT OR REPLACE INTO nodes 
                (id, type, name, fqn, file_path, start_line, end_line,
                 signature, return_type, docstring, is_exported, is_async,
                 is_test, raw_body, skeleton_standard, skeleton_minimal, language,
                 module, workspace_id, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
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
            if node.get('docstring'): vec_text += f"Doc: {node['docstring']}\n"
            vec_text += node['raw_body'][:1000]
            
            vector_items.append({
                "id": node["id"], "text": vec_text,
                "meta": {"module": mod_name, "file": rel_path, "type": node["type"], "category": cat}
            })
            
        # 엣지 저장
        for edge in result["edges"]:
            conn.execute("INSERT OR IGNORE INTO edges (source_id, target_id, type) VALUES (?, ?, ?)",
                         (edge["source_id"], edge["target_id"], edge.get("type", "CALLS")))
            
        conn.execute("INSERT OR REPLACE INTO file_cache (file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)",
                     (rel_path, file_hash, int(time.time()), workspace_id))
        stats["indexed"] += 1

    # 벡터 인덱싱
    if vector_items:
        sys.stderr.write(f"[cortex-indexer] Vectorizing {len(vector_items)} nodes...\n")
        ve.index_texts(workspace, vector_items, use_gpu=True)

    _resolve_edges(conn)
    _cleanup_deleted_files(conn, files)
    conn.commit()
    conn.close()
    return stats


def _resolve_edges(conn):
    unresolved = conn.execute("SELECT rowid, target_id FROM edges WHERE target_id LIKE '__unresolved__%'").fetchall()
    for row_id, target_ref in unresolved:
        target_name = target_ref.replace("__unresolved__::", "")
        match = conn.execute("SELECT id FROM nodes WHERE name = ? LIMIT 1", (target_name,)).fetchone()
        if match:
            try:
                conn.execute("UPDATE edges SET target_id = ? WHERE rowid = ?", (match[0], row_id))
            except sqlite3.IntegrityError:
                # 중복된 관계가 이미 존재하는 경우, 현재의 미해결 관계 행을 삭제하여 중복 방지
                conn.execute("DELETE FROM edges WHERE rowid = ?", (row_id,))
        else:
            conn.execute("DELETE FROM edges WHERE rowid = ?", (row_id,))


def _cleanup_deleted_files(conn, current_files: list):
    cached_files = conn.execute("SELECT file_path FROM file_cache").fetchall()
    current_set = set(current_files)
    for (cached_path,) in cached_files:
        if cached_path not in current_set:
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (cached_path,))
            conn.execute("DELETE FROM file_cache WHERE file_path = ?", (cached_path,))


if __name__ == "__main__":
    import json
    workspace = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print(f"Indexing: {workspace}")
    stats = index_workspace(workspace, force="--force" in sys.argv)
    print(json.dumps(stats, indent=2))
