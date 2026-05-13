"""Vector persistence helpers for parsed index records."""

from __future__ import annotations


from cortex.indexing.queries import (
    UPSERT_VEC_NODES_SQL,
    select_node_id_rowid_by_ids_sql,
)


def dedupe_vector_items(vector_items: list[dict]) -> list[dict]:
    """Deduplicate vector payloads by node ID while preserving latest content."""
    if not vector_items:
        return []
    return list({item["id"]: item for item in vector_items}.values())


def persist_node_vectors(conn, vector_items: list[dict], *, use_gpu: bool | None = None) -> None:
    """Generate embeddings and persist them in vec_nodes."""
    vector_items = dedupe_vector_items(vector_items)
    if not vector_items:
        return

    from cortex.embeddings import get_embeddings

    ids = [item["id"] for item in vector_items]
    placeholders = ",".join("?" * len(ids))
    rowid_rows = conn.execute(
        select_node_id_rowid_by_ids_sql(placeholders),
        ids,
    ).fetchall()
    id_to_rowid = {row[0]: row[1] for row in rowid_rows}

    texts = [item["text"] for item in vector_items]
    embeddings = get_embeddings(texts, use_gpu=use_gpu)
    vec_rows = []
    for item, embedding in zip(vector_items, embeddings):
        rowid = id_to_rowid.get(item["id"])
        if rowid is not None:
            vec_rows.append((rowid, embedding.tobytes()))

    if vec_rows:
        conn.executemany(UPSERT_VEC_NODES_SQL, vec_rows)
        conn.commit()



__all__ = ["dedupe_vector_items", "persist_node_vectors"]
