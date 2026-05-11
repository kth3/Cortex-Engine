"""Cleanup helpers for index database state."""

from __future__ import annotations

from cortex.logger import get_logger

log = get_logger("indexing.cleanup")


def cleanup_file_records(conn, rel_path: str) -> list[str]:
    """Remove all DB rows owned by one indexed file and return removed node IDs."""
    old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (rel_path,)).fetchall()
    old_ids = [row[0] for row in old_nodes]

    if old_ids:
        chunk_size = 900
        for index in range(0, len(old_ids), chunk_size):
            chunk = old_ids[index:index + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM edges WHERE source_id IN ({placeholders})", chunk)
            conn.execute(f"DELETE FROM edges WHERE target_id IN ({placeholders})", chunk)
        conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))

    conn.execute("DELETE FROM file_cache WHERE file_path = ?", (rel_path,))
    return old_ids


def cleanup_deleted_files(workspace: str, conn, current_files: list[str]) -> None:
    """Remove DB records for files that no longer exist on disk."""
    cached_files = conn.execute("SELECT file_path FROM file_cache").fetchall()
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
