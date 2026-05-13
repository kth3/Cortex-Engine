"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
from pathlib import Path
import yaml
from cortex import db as pc_db
from cortex import indexer as pc_indexer
from cortex import paths as pc_paths
from cortex.indexer_utils import load_settings, scan_files

def _read_local_settings(ctx):
    _, local_path = pc_paths.settings_paths(ctx.workspace)
    if not local_path.exists():
        return {}, local_path
    with open(local_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}, local_path

def _write_local_settings(data, local_path):
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

def _validated_index_root(ctx, raw_path):
    if not raw_path or not str(raw_path).strip():
        raise ValueError("index root path is required")
    if any(ch in str(raw_path) for ch in "*?"):
        raise ValueError("glob patterns are not allowed for index_roots")

    ws = Path(ctx.workspace).resolve()
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

def _index_roots_scan_count(ctx, candidate_roots):
    settings = load_settings(ctx.workspace)
    settings.setdefault("indexing_rules", {})["index_roots"] = candidate_roots
    return len(scan_files(ctx.workspace, pc_indexer.SUPPORTED_EXTENSIONS, settings_override=settings))

def call_pc_index_roots_list(ctx, args):
    settings = load_settings(ctx.workspace)
    roots = _effective_index_roots(settings)
    ws = Path(ctx.workspace).resolve()
    resolved = []
    for root in roots:
        target = ws if root == "." else (ws / root).resolve()
        resolved.append({"path": root, "absolute": str(target), "exists": target.exists()})
    _, local_path = pc_paths.settings_paths(ctx.workspace)
    return {"index_roots": roots, "resolved": resolved, "settings_local": str(local_path)}

def call_pc_index_roots_add(ctx, args):
    dry_run = args.get("dry_run", True)
    root = _validated_index_root(ctx, args["path"])
    local_settings, local_path = _read_local_settings(ctx)
    roots = _effective_index_roots(load_settings(ctx.workspace))
    if root not in roots:
        roots.append(root)
    scan_count = _index_roots_scan_count(ctx, roots)
    if not dry_run:
        local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
        _write_local_settings(local_settings, local_path)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}

def call_pc_index_roots_remove(ctx, args):
    dry_run = args.get("dry_run", True)
    root = _validated_index_root(ctx, args["path"])
    local_settings, local_path = _read_local_settings(ctx)
    roots = [r for r in _effective_index_roots(load_settings(ctx.workspace)) if r != root]
    scan_count = _index_roots_scan_count(ctx, roots)
    if not dry_run:
        local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
        _write_local_settings(local_settings, local_path)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}

def call_pc_reindex(ctx, args):
    return pc_indexer.index_workspace(ctx.workspace, force=args.get("force", False))

def call_pc_index_status(ctx, args):
    conn = pc_db.get_connection(ctx.workspace)
    try:
        return pc_db.get_stats(conn)
    finally:
        conn.close()
