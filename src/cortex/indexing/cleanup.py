"""Cleanup helpers for index database state."""

from __future__ import annotations

from cortex.logger import get_logger
from cortex.indexing.queries import (
    SELECT_NODES_ID_BY_PATH_SQL,
    DELETE_NODES_BY_PATH_SQL,
    DELETE_FILE_CACHE_BY_PATH_SQL,
    SELECT_ALL_FILE_CACHE_PATHS_SQL,
    delete_edges_by_source_id_sql,
    delete_edges_by_target_id_sql,
)

log = get_logger("indexing.cleanup")


def cleanup_file_records(conn, rel_path: str) -> list[str]:
    """Remove all DB rows owned by one indexed file and return removed node IDs."""
    old_nodes = conn.execute(SELECT_NODES_ID_BY_PATH_SQL, (rel_path,)).fetchall()
    old_ids = [row[0] for row in old_nodes]

    if old_ids:
        chunk_size = 900
        for index in range(0, len(old_ids), chunk_size):
            chunk = old_ids[index:index + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(delete_edges_by_source_id_sql(placeholders), chunk)
            conn.execute(delete_edges_by_target_id_sql(placeholders), chunk)
        conn.execute(DELETE_NODES_BY_PATH_SQL, (rel_path,))

    conn.execute(DELETE_FILE_CACHE_BY_PATH_SQL, (rel_path,))
    return old_ids


def cleanup_deleted_files(workspace: str, conn, current_files: list[str]) -> None:
    """Remove DB records for files that no longer exist on disk."""
    cached_files = conn.execute(SELECT_ALL_FILE_CACHE_PATHS_SQL).fetchall()
    db_file_list = [row[0] for row in cached_files]
    current_file_set = set(current_files)
    deleted_files = [file_path for file_path in db_file_list if file_path not in current_file_set]

    if not deleted_files:
        return

    log.info("Found %d deleted files. Cleaning up DB...", len(deleted_files))
    for deleted_path in deleted_files:
        cleanup_file_records(conn, deleted_path)

    conn.commit()
    log.info("Cleanup complete for %d files.", len(deleted_files))


__all__ = ["cleanup_deleted_files", "cleanup_file_records"]
