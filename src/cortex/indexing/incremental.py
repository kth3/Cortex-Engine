"""Opportunistic incremental indexing pipeline."""
from __future__ import annotations

import time

from cortex.indexing.workspace import index_workspace
from cortex.logger import get_logger

log = get_logger("indexer")

_last_opportunistic_check = 0.0
OPPORTUNISTIC_COOLDOWN = 60


def incremental_index_changed(workspace: str) -> dict:
    """경량 증분 인덱싱: Rust workspace 인덱서를 재사용한다."""
    global _last_opportunistic_check

    now = time.time()
    if now - _last_opportunistic_check < OPPORTUNISTIC_COOLDOWN:
        return {"status": "cooldown"}
    _last_opportunistic_check = now

    stats = index_workspace(workspace, force=False)
    if stats.get("indexed", 0) == 0 and stats.get("deleted", 0) == 0:
        return {"status": "clean", "checked_files": stats.get("total_files", 0)}

    log.info(
        "Opportunistic indexing complete: %d files indexed (%d skipped).",
        stats.get("indexed", 0),
        stats.get("skipped", 0),
    )
    return {
        "status": "indexed",
        "changed": stats.get("indexed", 0) + stats.get("deleted", 0),
        "indexed": stats.get("indexed", 0),
    }
