"""Workspace indexing pipeline."""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from cortex import storage as db
from cortex.embeddings import batch_vectorize_memories, batch_vectorize_nodes, detect_gpu
from cortex.indexing.cleanup import cleanup_deleted_files
from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.file_pipeline import index_file, read_text_file
from cortex.indexing.queries import DELETE_FILE_CACHE_SQL, SELECT_FILE_CACHE_SQL, UPSERT_LAST_INDEXED_AT_SQL
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.logger import get_logger
from cortex.scanner.finder import scan_files
from cortex.utils.text import compute_hash

log = get_logger("indexer")


def _sync_skills(workspace):
    from cortex.skills.manager import SkillManager
    log.info("Auto-syncing skills to memories DB...")
    try:
        sm = SkillManager(workspace)
        sm.sync_skills(workspace)
    except Exception as e:
        log.warning("Skill sync failed: %s", e)


def _load_file_cache(conn, force):
    if force:
        conn.execute(DELETE_FILE_CACHE_SQL)
        conn.commit()
        return {}

    cached_rows = conn.execute(SELECT_FILE_CACHE_SQL).fetchall()
    return {row[0]: row[1] for row in cached_rows}


def _vector_prefix_for_path(rel_path):
    parts = Path(rel_path).parts
    prefix = "root"
    if len(parts) > 1 and not parts[0].startswith("."):
        prefix = parts[0]
    return prefix


def _collect_index_result(stats, all_vector_items_by_prefix, rel_path, res):
    if "error" in res:
        stats["errors"] += 1
        return

    stats["indexed"] += 1
    prefix = _vector_prefix_for_path(rel_path)
    if prefix not in all_vector_items_by_prefix:
        all_vector_items_by_prefix[prefix] = []
    all_vector_items_by_prefix[prefix].extend(res.get("_vector_items", []))


def _release_local_cuda_model_after_indexing() -> None:
    """Release only a local CUDA fallback embedding model."""
    try:
        from cortex.embeddings import provider

        if getattr(provider, "_model_device", None) != "cuda":
            return

        from cortex.embeddings.hardware import release_gpu

        release_gpu()
        log.info("Local CUDA embedding model released after full indexing.")
    except Exception:
        log.debug("Local CUDA embedding model release skipped.", exc_info=True)


def _sync_graph_from_sqlite(workspace, conn):
    try:
        from cortex.storage.graph import GraphDB
        gdb = GraphDB(workspace)
        log.info("Building Kuzu graph from SQLite edges...")
        g_stats = gdb.build_from_sqlite(conn)
        log.info("Kuzu graph built: %d nodes, %d edges, %d errors", g_stats['nodes'], g_stats['edges'], g_stats['errors'])
    except Exception as e:
        log.warning("Kuzu graph build failed: %s", e)


def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 하이브리드 인덱싱."""
    _sync_skills(workspace)

    files = scan_files(workspace, SUPPORTED_EXTENSIONS)
    conn = db.get_connection(workspace)
    db.init_schema(conn)

    cleanup_deleted_files(workspace, conn, files)

    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}
    all_vector_items_by_prefix = {}

    cache_dict = _load_file_cache(conn, force)

    for rel_path in files:
        full_path = os.path.join(workspace, rel_path)
        try:
            source = read_text_file(full_path)
        except Exception:
            stats["errors"] += 1
            continue

        file_hash = compute_hash(source)
        if not force:
            cached_hash = cache_dict.get(rel_path)
            if cached_hash == file_hash:
                stats["skipped"] += 1
                continue

        res = index_file(workspace, rel_path, conn=conn, vectorize=False)
        _collect_index_result(stats, all_vector_items_by_prefix, rel_path, res)

    use_gpu = detect_gpu()
    if all_vector_items_by_prefix:
        batch_vectorize_nodes(conn, all_vector_items_by_prefix, use_gpu, workspace=workspace)

    sync_rules_to_memories(workspace, conn)

    try:
        batch_vectorize_memories(conn, use_gpu, workspace=workspace)
    except Exception as e:
        log.error("Failed to index memories table: %s", e)

    _release_local_cuda_model_after_indexing()

    conn.execute(
        UPSERT_LAST_INDEXED_AT_SQL,
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()

    resolve_unresolved_edges(conn)
    _sync_graph_from_sqlite(workspace, conn)

    conn.close()
    return stats
