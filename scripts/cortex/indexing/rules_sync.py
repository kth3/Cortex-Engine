"""Synchronize repository rule/reference documents into memories."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cortex.indexer_utils import compute_hash, strip_frontmatter
from cortex.logger import get_logger

log = get_logger("indexing.rules_sync")


RULE_DIRS = {
    "rule": (".agents", "rules"),
    "protocol": (".agents", "rules", "core", "protocols"),
    "resource": (".agents", "knowledge", "resources"),
    "example": (".agents", "knowledge", "examples"),
    "success_pattern": (".agents", "docs", "success_patterns"),
    "anti_pattern": (".agents", "docs", "anti_patterns"),
    "insight": (".agents", "docs", "insights"),
    "architecture": (".agents", "docs", "architecture"),
    "reference": ("references",),
}


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _category_dirs(workspace: str) -> dict[str, Path]:
    return {category: Path(workspace, *parts) for category, parts in RULE_DIRS.items()}


def _category_exclusions(category: str, own_root: Path, resolved_dirs: dict[str, Path]) -> list[Path]:
    return [
        other
        for other_category, other in resolved_dirs.items()
        if other_category != category and other != own_root and _within(other, own_root)
    ]


def _iter_category_markdown(category: str, dir_path: Path, resolved_dirs: dict[str, Path]) -> list[Path]:
    if not dir_path.is_dir():
        return []
    own_root = resolved_dirs[category]
    exclusions = _category_exclusions(category, own_root, resolved_dirs)
    return [
        path
        for path in dir_path.rglob("*.md")
        if not any(_within(path.resolve(), exclusion) for exclusion in exclusions)
    ]


def _extract_title(content: str, fallback: str) -> str:
    for line in content.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _upsert_memory(conn, category: str, md_path: Path, content: str) -> bool:
    key = f"{category}::{md_path.stem}"
    content_clean = strip_frontmatter(content).strip()
    if not content_clean:
        return False

    content_hash = compute_hash(content_clean)
    existing = conn.execute("SELECT content FROM memories WHERE key = ?", (key,)).fetchone()
    if existing and compute_hash(existing[0]) == content_hash:
        return False

    now = int(time.time())
    tags_json = json.dumps([category, "agent-rule"], ensure_ascii=False)
    rel_json = json.dumps({}, ensure_ascii=False)
    title = _extract_title(content_clean, md_path.stem)
    prefixed_content = f"[{category.upper()}] {title}\n{content_clean}"
    conn.execute(
        """INSERT INTO memories (key, project_id, category, content, tags, relationships, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
           content=excluded.content, category=excluded.category,
           tags=excluded.tags, updated_at=excluded.updated_at""",
        (key, ".", category, prefixed_content, tags_json, rel_json, now, now),
    )
    return True


def _disk_keys(category_dirs: dict[str, Path], resolved_dirs: dict[str, Path]) -> set[str]:
    keys = set()
    for category, dir_path in category_dirs.items():
        for md_path in _iter_category_markdown(category, dir_path, resolved_dirs):
            keys.add(f"{category}::{md_path.stem}")
    return keys


def _delete_orphan_memories(conn, managed_categories: list[str], all_disk_keys: set[str]) -> int:
    placeholders = ",".join("?" * len(managed_categories))
    db_keys = [
        row[0]
        for row in conn.execute(
            f"SELECT key FROM memories WHERE category IN ({placeholders}) AND tags LIKE '%agent-rule%'",
            managed_categories,
        ).fetchall()
    ]

    orphans = [key for key in db_keys if key not in all_disk_keys]
    for index in range(0, len(orphans), 900):
        chunk = orphans[index:index + 900]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM memories WHERE key IN ({placeholders})", chunk)
    return len(orphans)


def sync_rules_to_memories(workspace: str, conn) -> None:
    """Sync managed rule/protocol/reference Markdown files into the memories table."""
    from tqdm import tqdm

    category_dirs = _category_dirs(workspace)
    resolved_dirs = {category: path.resolve() for category, path in category_dirs.items()}
    synced = 0

    for category, dir_path in category_dirs.items():
        md_files = _iter_category_markdown(category, dir_path, resolved_dirs)
        for md_path in tqdm(md_files, desc=f"Syncing {category}", unit="file"):
            try:
                content = md_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if _upsert_memory(conn, category, md_path, content):
                synced += 1

    if synced > 0:
        conn.commit()
        log.info("Synced %d rule/protocol docs to memories table.", synced)

    orphan_count = _delete_orphan_memories(conn, list(category_dirs.keys()), _disk_keys(category_dirs, resolved_dirs))
    if orphan_count:
        conn.commit()
        log.info("GC: removed %d orphaned rule/protocol entries.", orphan_count)


__all__ = ["RULE_DIRS", "sync_rules_to_memories"]
