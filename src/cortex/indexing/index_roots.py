"""index_roots 설정과 실제 스캔 경로를 정규화하는 공용 유틸리티."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex import paths as pc_paths

from .index_roots_support import (
    EXTERNAL_ROOT_PREFIX,
    effective_index_roots,
    external_db_root,
    normalize_alias,
    normalize_configured_index_roots,
    read_local_settings,
    reject_dangerous_parts,
    relative_root_text,
    require_index_root_path,
    resolve_target,
    root_identity,
    write_local_settings,
)


@dataclass(frozen=True)
class IndexRoot:
    db_root: str
    source_path: Path
    external: bool = False
    alias: str | None = None


def set_local_index_roots(local_settings: dict[str, Any], local_path: Path, roots: list[Any]) -> None:
    local_settings.setdefault("indexing_rules", {})["index_roots"] = roots
    write_local_settings(local_settings, local_path)


def build_index_root_entry(workspace: str, raw_path: Any, alias: Any = None) -> tuple[Any, IndexRoot]:
    raw_text = require_index_root_path(raw_path)
    ws, target = resolve_target(workspace, raw_text)

    try:
        rel_text = relative_root_text(ws, target)
    except ValueError:
        root_alias = normalize_alias(alias, target)
        reject_dangerous_parts(target.name)
        entry = {"path": str(target).replace("\\", "/"), "alias": root_alias, "external": True}
        return entry, IndexRoot(external_db_root(root_alias), target, external=True, alias=root_alias)

    reject_dangerous_parts(rel_text)
    return rel_text, IndexRoot(rel_text, target, external=False)


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

    if root_identity(entry) not in {root_identity(root) for root in roots}:
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
        if not matches and root_identity(root) == root_identity(target_text):
            matches = True

        if matches and removed is None:
            removed = root
            continue
        remaining.append(root)

    return remaining, removed
