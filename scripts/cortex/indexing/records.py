"""Node/edge persistence helpers for the indexing pipeline."""

from __future__ import annotations

from collections.abc import Iterable


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
    conn.executemany("""
        INSERT OR REPLACE INTO nodes
        (id, type, name, fqn, file_path, start_line, end_line,
         signature, return_type, docstring, is_exported, is_async,
         is_test, raw_body, skeleton_standard, skeleton_minimal, language,
         module, workspace_id, category)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, node_rows)


def insert_edges(conn, edges: Iterable[dict]) -> None:
    """Persist parser edge rows while ignoring duplicate edge triplets."""
    edge_rows = [(edge["source_id"], edge["target_id"], edge.get("type", "CALLS")) for edge in edges]
    if edge_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO edges (source_id, target_id, type) VALUES (?, ?, ?)",
            edge_rows,
        )


def upsert_file_cache(conn, rel_path: str, source_hash: str, indexed_at: int, workspace_id: str) -> None:
    """Record the last indexed hash for a file."""
    conn.execute(
        "INSERT OR REPLACE INTO file_cache (file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)",
        (rel_path, source_hash, indexed_at, workspace_id),
    )


__all__ = ["build_node_rows", "insert_edges", "insert_nodes", "upsert_file_cache"]
