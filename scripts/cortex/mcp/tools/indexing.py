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

DEFAULT_INDEX_ROOTS = (".",)
DISALLOWED_INDEX_ROOT_GLOB_CHARS = "*?"
DANGEROUS_INDEX_ROOT_PARTS = frozenset({".git", "node_modules", "library", "temp"})


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


def _write_local_index_roots(local_settings, local_path, roots) -> None:
    local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
    _write_local_settings(local_settings, local_path)


def _effective_index_roots(settings):
    rules = settings.get("indexing_rules", {}) or {}
    roots = rules.get("index_roots")
    if roots is None:
        roots = list(DEFAULT_INDEX_ROOTS)
    if isinstance(roots, str):
        roots = [roots]
    return list(dict.fromkeys(roots or []))


def _require_index_root_path(raw_path) -> str:
    raw_text = str(raw_path).strip() if raw_path is not None else ""
    if not raw_text:
        raise ValueError("index root path is required")
    if any(ch in raw_text for ch in DISALLOWED_INDEX_ROOT_GLOB_CHARS):
        raise ValueError("glob patterns are not allowed for index_roots")
    return raw_text


def _resolve_index_root(workspace: str, raw_text: str) -> tuple[Path, Path]:
    ws = Path(workspace).resolve()
    raw = Path(raw_text).expanduser()
    target = raw.resolve() if raw.is_absolute() else (ws / raw).resolve()
    target.relative_to(ws)
    return ws, target


def _relative_index_root_text(workspace_path: Path, target: Path) -> str:
    rel = target.relative_to(workspace_path)
    if str(rel) == ".":
        return "."
    return str(rel).replace("\\", "/")


def _reject_dangerous_index_root(rel_text: str) -> None:
    parts = {p.lower() for p in Path(rel_text).parts}
    if rel_text != "." and parts & DANGEROUS_INDEX_ROOT_PARTS:
        raise ValueError("dangerous index root rejected")


def _validated_index_root(ctx, raw_path):
    raw_text = _require_index_root_path(raw_path)
    ws, target = _resolve_index_root(ctx.workspace, raw_text)
    rel_text = _relative_index_root_text(ws, target)
    _reject_dangerous_index_root(rel_text)
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
        _write_local_index_roots(local_settings, local_path, roots)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}


def call_pc_index_roots_remove(ctx, args):
    dry_run = args.get("dry_run", True)
    root = _validated_index_root(ctx, args["path"])
    local_settings, local_path = _read_local_settings(ctx)
    roots = [r for r in _effective_index_roots(load_settings(ctx.workspace)) if r != root]
    scan_count = _index_roots_scan_count(ctx, roots)
    if not dry_run:
        _write_local_index_roots(local_settings, local_path, roots)
    return {"executed": not dry_run, "index_roots": roots, "scan_count": scan_count, "settings_local": str(local_path)}


def call_pc_reindex(ctx, args):
    return pc_indexer.index_workspace(ctx.workspace, force=args.get("force", False))


def call_pc_index_status(ctx, args):
    conn = pc_db.get_connection(ctx.workspace)
    try:
        return pc_db.get_stats(conn)
    finally:
        conn.close()
