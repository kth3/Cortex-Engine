"""Workspace indexing pipeline."""
from __future__ import annotations

import datetime
import json
import subprocess

from cortex import storage as db
from cortex.embeddings import batch_vectorize_memories, batch_vectorize_nodes, detect_gpu
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.logger import get_logger
from cortex.runtime.paths import ensure_rust_watcher_binary

log = get_logger("indexer")


def _sync_skills(workspace):
    from cortex.skills.manager import SkillManager

    log.info("Auto-syncing skills to memories DB...")
    try:
        sm = SkillManager(workspace)
        sm.sync_skills(workspace)
    except Exception as e:
        log.warning("Skill sync failed: %s", e)


def _run_rust_index(workspace: str, force: bool) -> dict:
    binary = ensure_rust_watcher_binary()
    command = [
        str(binary),
        "index",
        "--workspace",
        workspace,
    ]
    if force:
        command.append("--force")

    proc = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout or "{}")


def _sync_graph_from_sqlite(workspace, conn):
    gdb = None
    try:
        from cortex.storage.graph import GraphDB

        gdb = GraphDB(workspace)
        log.info("Building Kuzu graph from SQLite edges...")
        g_stats = gdb.build_from_sqlite(conn)
        log.info(
            "Kuzu graph built: %d nodes, %d edges, %d errors",
            g_stats["nodes"],
            g_stats["edges"],
            g_stats["errors"],
        )
    except Exception as e:
        log.warning("Kuzu graph build failed: %s", e)
    finally:
        if gdb is not None:
            del gdb


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


def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 하이브리드 인덱싱."""
    _sync_skills(workspace)

    report = _run_rust_index(workspace, force=force)
    stats = {
        "total_files": int(report.get("total_files", 0)),
        "indexed": int(report.get("indexed", 0)),
        "skipped": int(report.get("skipped", 0)),
        "errors": int(report.get("errors", 0)),
        "deleted": int(report.get("deleted", 0)),
    }
    all_vector_items_by_prefix = report.get("vector_items_by_prefix") or {}

    conn = db.get_connection(workspace)
    try:
        db.init_schema(conn)

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
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_at', ?)",
            (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.commit()

        _sync_graph_from_sqlite(workspace, conn)
    finally:
        conn.close()
    return stats
