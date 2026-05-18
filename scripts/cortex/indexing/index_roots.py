"""index_roots 설정과 실제 스캔 경로를 정규화하는 공용 유틸리티."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cortex import paths as pc_paths

DEFAULT_INDEX_ROOTS = (".",)
DISALLOWED_INDEX_ROOT_GLOB_CHARS = "*?"
DANGEROUS_INDEX_ROOT_PARTS = frozenset({".git", "node_modules", "library", "temp"})
EXTERNAL_ROOT_PREFIX = "@external"


@dataclass(frozen=True)
class IndexRoot:
    db_root: str
    source_path: Path
    external: bool = False
    alias: str | None = None


def read_local_settings(workspace: str) -> tuple[dict[str, Any], Path]:
    _, local_path = pc_paths.settings_paths(workspace)
    if not local_path.exists():
        return {}, local_path
    with open(local_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}, local_path


def write_local_settings(data: dict[str, Any], local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def effective_index_roots(settings: dict[str, Any]) -> list[Any]:
    rules = settings.get("indexing_rules", {}) or {}
    roots = rules.get("index_roots")
    if roots is None:
        roots = list(DEFAULT_INDEX_ROOTS)
    if isinstance(roots, (str, dict)):
        roots = [roots]

    unique: list[Any] = []
    seen = set()
    for root in roots or []:
        key = _root_identity(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def set_local_index_roots(local_settings: dict[str, Any], local_path: Path, roots: list[Any]) -> None:
    local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
    write_local_settings(local_settings, local_path)


def require_index_root_path(raw_path: Any) -> str:
    raw_text = str(raw_path).strip() if raw_path is not None else ""
    if not raw_text:
        raise ValueError("index root path is required")
    if any(ch in raw_text for ch in DISALLOWED_INDEX_ROOT_GLOB_CHARS):
        raise ValueError("glob patterns are not allowed for index_roots")
    return raw_text


def normalize_alias(raw_alias: Any, target: Path) -> str:
    alias = str(raw_alias).strip() if raw_alias is not None else ""
    if not alias:
        alias = target.name
    if not alias:
        raise ValueError("external index root alias is required")
    if any(ch in alias for ch in "/\\:*?\"<>|"):
        raise ValueError("external index root alias contains invalid characters")
    return alias


def _resolve_target(workspace: str, raw_text: str) -> tuple[Path, Path]:
    ws = Path(workspace).resolve()
    raw = Path(raw_text).expanduser()
    target = raw.resolve() if raw.is_absolute() else (ws / raw).resolve()
    return ws, target


def _relative_root_text(workspace_path: Path, target: Path) -> str:
    rel = target.relative_to(workspace_path)
    if str(rel) == ".":
        return "."
    return str(rel).replace("\\", "/")


def _reject_dangerous_parts(path_text: str) -> None:
    parts = {p.lower() for p in Path(path_text).parts}
    if path_text != "." and parts & DANGEROUS_INDEX_ROOT_PARTS:
        raise ValueError("dangerous index root rejected")


def _external_db_root(alias: str) -> str:
    return f"{EXTERNAL_ROOT_PREFIX}/{alias}"


def _root_identity(root: Any) -> tuple[Any, ...]:
    if isinstance(root, dict):
        return (
            "dict",
            bool(root.get("external")),
            str(root.get("alias", "")).casefold(),
            str(root.get("path", "")).replace("\\", "/").casefold(),
        )
    return ("str", str(root).replace("\\", "/").casefold())


def build_index_root_entry(workspace: str, raw_path: Any, alias: Any = None) -> tuple[Any, IndexRoot]:
    raw_text = require_index_root_path(raw_path)
    ws, target = _resolve_target(workspace, raw_text)

    try:
        rel_text = _relative_root_text(ws, target)
    except ValueError:
        root_alias = normalize_alias(alias, target)
        _reject_dangerous_parts(target.name)
        entry = {
            "path": str(target).replace("\\", "/"),
            "alias": root_alias,
            "external": True,
        }
        return entry, IndexRoot(_external_db_root(root_alias), target, external=True, alias=root_alias)

    _reject_dangerous_parts(rel_text)
    return rel_text, IndexRoot(rel_text, target, external=False)


def normalize_configured_index_roots(workspace: str, settings: dict[str, Any]) -> list[IndexRoot]:
    ws = Path(workspace).resolve()
    normalized: list[IndexRoot] = []
    seen = set()

    for root in effective_index_roots(settings):
        if isinstance(root, dict):
            raw_text = require_index_root_path(root.get("path"))
            _, target = _resolve_target(workspace, raw_text)
            root_alias = normalize_alias(root.get("alias"), target)
            db_root = _external_db_root(root_alias) if root.get("external") else _relative_root_text(ws, target)
            _reject_dangerous_parts(target.name if root.get("external") else db_root)
            index_root = IndexRoot(db_root, target, external=bool(root.get("external")), alias=root_alias if root.get("external") else None)
        else:
            raw_text = require_index_root_path(root)
            _, target = _resolve_target(workspace, raw_text)
            try:
                db_root = _relative_root_text(ws, target)
            except ValueError:
                continue
            _reject_dangerous_parts(db_root)
            index_root = IndexRoot(db_root, target, external=False)

        if index_root.db_root in seen:
            continue
        seen.add(index_root.db_root)
        normalized.append(index_root)

    return normalized


def source_path_for_index_path(workspace: str, db_path: str, settings: dict[str, Any]) -> Path:
    db_text = db_path.replace("\\", "/")
    if not db_text.startswith(f"{EXTERNAL_ROOT_PREFIX}/"):
        return Path(workspace).resolve() / db_path

    for root in normalize_configured_index_roots(workspace, settings):
        if not root.external:
            continue
        prefix = f"{root.db_root}/"
        if db_text == root.db_root:
            return root.source_path
        if db_text.startswith(prefix):
            return root.source_path / db_text[len(prefix):]

    raise FileNotFoundError(f"external index root not configured for {db_path}")


def plan_index_roots_list(workspace: str, settings: dict[str, Any]) -> dict[str, Any]:
    roots = effective_index_roots(settings)
    resolved = []
    for root in normalize_configured_index_roots(workspace, settings):
        resolved.append(
            {
                "path": root.db_root,
                "absolute": str(root.source_path),
                "exists": root.source_path.exists(),
                "external": root.external,
                "alias": root.alias,
            }
        )
    _, local_path = pc_paths.settings_paths(workspace)
    return {"index_roots": roots, "resolved": resolved, "settings_local": str(local_path)}


def add_index_root(workspace: str, settings: dict[str, Any], raw_path: Any, alias: Any = None) -> tuple[list[Any], Any, IndexRoot]:
    entry, index_root = build_index_root_entry(workspace, raw_path, alias)
    roots = effective_index_roots(settings)

    if index_root.external:
        for existing in normalize_configured_index_roots(workspace, settings):
            if existing.external and existing.alias.casefold() == index_root.alias.casefold():
                raise ValueError(f"external index root alias already exists: {index_root.alias}")

    if _root_identity(entry) not in {_root_identity(root) for root in roots}:
        roots.append(entry)
    return roots, entry, index_root


def remove_index_root(workspace: str, settings: dict[str, Any], target: Any) -> tuple[list[Any], Any | None]:
    target_text = require_index_root_path(target)
    roots = effective_index_roots(settings)
    remaining: list[Any] = []
    removed = None

    for root in roots:
        normalized = normalize_configured_index_roots(workspace, {"indexing_rules": {"index_roots": [root]}})
        index_root = normalized[0] if normalized else None
        matches = False
        if index_root is not None:
            matches = (
                target_text == index_root.db_root
                or target_text == str(index_root.source_path)
                or (index_root.alias is not None and target_text.casefold() == index_root.alias.casefold())
            )
        if not matches and _root_identity(root) == _root_identity(target_text):
            matches = True

        if matches and removed is None:
            removed = root
            continue
        remaining.append(root)

    return remaining, removed
