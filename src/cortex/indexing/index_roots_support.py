"""Shared helpers for index root configuration and normalization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cortex import paths as pc_paths

DEFAULT_INDEX_ROOTS = (".",)
DISALLOWED_INDEX_ROOT_GLOB_CHARS = "*?"
DANGEROUS_INDEX_ROOT_PARTS = frozenset({".git", "node_modules", "library", "temp"})
EXTERNAL_ROOT_PREFIX = "@external"


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
        key = root_identity(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


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


def resolve_target(workspace: str, raw_text: str) -> tuple[Path, Path]:
    ws = Path(workspace).resolve()
    raw = Path(raw_text).expanduser()
    target = raw.resolve() if raw.is_absolute() else (ws / raw).resolve()
    return ws, target


def relative_root_text(workspace_path: Path, target: Path) -> str:
    rel = target.relative_to(workspace_path)
    if str(rel) == ".":
        return "."
    return str(rel).replace("\\", "/")


def reject_dangerous_parts(path_text: str) -> None:
    parts = {p.lower() for p in Path(path_text).parts}
    if path_text != "." and parts & DANGEROUS_INDEX_ROOT_PARTS:
        raise ValueError("dangerous index root rejected")


def external_db_root(alias: str) -> str:
    return f"{EXTERNAL_ROOT_PREFIX}/{alias}"


def root_identity(root: Any) -> tuple[Any, ...]:
    if isinstance(root, dict):
        return (
            "dict",
            bool(root.get("external")),
            str(root.get("alias", "")).casefold(),
            str(root.get("path", "")).replace("\\", "/").casefold(),
        )
    return ("str", str(root).replace("\\", "/").casefold())


def normalize_configured_index_roots(workspace: str, settings: dict[str, Any]) -> list[Any]:
    from .index_roots import IndexRoot  # local import to avoid circular dependency

    ws = Path(workspace).resolve()
    normalized: list[IndexRoot] = []
    seen = set()

    for root in effective_index_roots(settings):
        if isinstance(root, dict):
            raw_text = require_index_root_path(root.get("path"))
            _, target = resolve_target(workspace, raw_text)
            root_alias = normalize_alias(root.get("alias"), target)
            db_root = external_db_root(root_alias) if root.get("external") else relative_root_text(ws, target)
            reject_dangerous_parts(target.name if root.get("external") else db_root)
            index_root = IndexRoot(db_root, target, external=bool(root.get("external")), alias=root_alias if root.get("external") else None)
        else:
            raw_text = require_index_root_path(root)
            _, target = resolve_target(workspace, raw_text)
            try:
                db_root = relative_root_text(ws, target)
            except ValueError:
                continue
            reject_dangerous_parts(db_root)
            index_root = IndexRoot(db_root, target, external=False)

        if index_root.db_root in seen:
            continue
        seen.add(index_root.db_root)
        normalized.append(index_root)

    return normalized

