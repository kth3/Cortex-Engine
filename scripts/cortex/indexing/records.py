"""Node/edge persistence helpers for the indexing pipeline.

- 책임: 파싱된 데이터(nodes, edges)와 상태 정보(file_cache)를 데이터베이스에 기록(DML)하는 계층이다.
- 주의: 이 파일은 스키마(DDL)를 변경하는 곳이 아니며, DB 스키마를 변경하지 않고 기존의 INSERT, UPDATE, UPSERT(INSERT OR REPLACE) 의미와 DML 계약을 그대로 유지해야 한다.
"""

from __future__ import annotations

from collections.abc import Iterable

from cortex.indexing.queries import (
    INSERT_EDGE_IGNORE_SQL,
    UPSERT_FILE_CACHE_ENTRY_SQL,
    UPSERT_NODE_SQL,
)


def build_node_rows(
    nodes: Iterable[dict],
    *,
    rel_path: str,
    clean_source: str,
    module_name: str,
    workspace_id: str,
) -> tuple[list[tuple], list[dict]]:
    """Convert parser nodes into DB rows and vectorization payloads."""
    category = "SKILL" if "skills/" in rel_path else ("RULE" if rel_path.startswith(".agents/") else "SOURCE")
    node_rows: list[tuple] = []
    vector_items: list[dict] = []

    for node in nodes:
        node_rows.append((
            node["id"], node["type"], node["name"], node["fqn"],
            node["file_path"], node["start_line"], node["end_line"],
            node.get("signature"), node.get("return_type"), node.get("docstring"),
            node.get("is_exported", 1), node.get("is_async", 0), node.get("is_test", 0),
            node["raw_body"], node.get("skeleton_standard"), node.get("skeleton_minimal"), node["language"],
            module_name, workspace_id, category,
        ))

        vector_text = f"{node['type']} {node['fqn']}\n"
        if node.get("signature"):
            vector_text += f"Sig: {node['signature']}\n"
        if category == "RULE":
            vector_text += clean_source[:1200]
        else:
            vector_text += (node.get("raw_body") or "")[:1200]

        vector_items.append({
            "id": node["id"],
            "text": vector_text,
            "meta": {"module": module_name, "file": rel_path, "type": node["type"], "category": category},
        })

    return node_rows, vector_items


def insert_nodes(conn, node_rows: list[tuple]) -> None:
    """Persist parsed node rows."""
    if not node_rows:
        return
    conn.executemany(UPSERT_NODE_SQL, node_rows)


def insert_edges(conn, edges: Iterable[dict]) -> None:
    """Persist parser edge rows while ignoring duplicate edge triplets."""
    edge_rows = [(edge["source_id"], edge["target_id"], edge.get("type", "CALLS")) for edge in edges]
    if edge_rows:
        conn.executemany(INSERT_EDGE_IGNORE_SQL, edge_rows)


def upsert_file_cache(conn, rel_path: str, source_hash: str, indexed_at: int, workspace_id: str) -> None:
    """Record the last indexed hash for a file."""
    conn.execute(
        UPSERT_FILE_CACHE_ENTRY_SQL,
        (rel_path, source_hash, indexed_at, workspace_id),
    )


__all__ = ["build_node_rows", "insert_edges", "insert_nodes", "upsert_file_cache"]
