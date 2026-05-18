"""Opportunistic incremental indexing pipeline."""
from __future__ import annotations

import datetime
import os
import time

from cortex import storage as db
from cortex.config.settings import load_settings
from cortex.embeddings import batch_vectorize_memories, batch_vectorize_nodes
from cortex.indexing.cleanup import cleanup_deleted_files
from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.file_pipeline import index_file
from cortex.indexing.index_roots import source_path_for_index_path
from cortex.indexing.queries import LAST_INDEXED_AT_SQL, UPSERT_LAST_INDEXED_AT_SQL
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.logger import get_logger
from cortex.scanner.finder import scan_files

log = get_logger("indexer")

_last_opportunistic_check = 0.0
OPPORTUNISTIC_COOLDOWN = 60


def incremental_index_changed(workspace: str) -> dict:
    """경량 증분 인덱싱: 마지막 인덱싱 이후 변경된 파일만 CPU로 즉석 처리."""
    global _last_opportunistic_check

    now = time.time()
    if now - _last_opportunistic_check < OPPORTUNISTIC_COOLDOWN:
        return {"status": "cooldown"}
    _last_opportunistic_check = now

    conn = db.get_connection(workspace)

    row = conn.execute(LAST_INDEXED_AT_SQL).fetchone()
    if not row:
        conn.close()
        return {"status": "skip", "reason": "no previous index"}

    last_indexed = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").timestamp()
    all_files = scan_files(workspace, SUPPORTED_EXTENSIONS)
    settings = load_settings(workspace)

    changed_files = []
    for rel_path in all_files:
        fpath = str(source_path_for_index_path(workspace, rel_path, settings))
        try:
            if os.path.exists(fpath) and os.path.getmtime(fpath) > last_indexed:
                changed_files.append(rel_path)
        except OSError:
            continue

    cleanup_deleted_files(workspace, conn, all_files)

    if not changed_files:
        conn.close()
        return {"status": "clean", "checked_files": len(all_files)}

    log.info("Opportunistic indexing: %d changed files detected (out of %d).", len(changed_files), len(all_files))
    indexed = 0
    vector_items = []
    for rel_path in changed_files:
        source_path = str(source_path_for_index_path(workspace, rel_path, settings))
        res = index_file(workspace, rel_path, conn=conn, vectorize=False, source_path=source_path)
        if "error" not in res:
            indexed += 1
            vector_items.extend(res.get("_vector_items", []))

    sync_rules_to_memories(workspace, conn)

    if vector_items:
        batch_vectorize_nodes(conn, {"opportunistic": vector_items}, use_gpu=False, workspace=workspace)

    try:
        batch_vectorize_memories(conn, use_gpu=False, workspace=workspace)
    except Exception:
        pass

    conn.execute(
        UPSERT_LAST_INDEXED_AT_SQL,
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()

    resolve_unresolved_edges(conn)
    conn.close()

    log.info("Opportunistic indexing complete: %d files indexed (CPU).", indexed)
    return {"status": "indexed", "changed": len(changed_files), "indexed": indexed}
