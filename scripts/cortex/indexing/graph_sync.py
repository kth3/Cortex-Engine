"""Graph database synchronization helpers for indexed files."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def sync_file_graph(
    workspace: str,
    module_name: str,
    rel_path: str,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> None:
    """Upsert one indexed file into the optional graph database backend.

    GraphDB is an optional integration. Import or connection failures are allowed
    to propagate to the caller's best-effort boundary.
    """
    from cortex.graph_db import GraphDB

    gdb = GraphDB(workspace)
    gdb.execute(
        "MERGE (m:Module {name: $name, file_path: $path})",
        {"name": module_name, "path": rel_path},
    )

    node_batch = [
        {
            "fqn": node["fqn"],
            "name": node["name"],
            "file_path": node["file_path"],
            "type": node["type"],
        }
        for node in nodes
        if node["type"] in ("Function", "Class")
    ]
    if node_batch:
        gdb.batch_upsert_nodes(node_batch)

    defines_batch = [
        {
            "src_fqn": module_name,
            "src_type": "Module",
            "tgt_fqn": node["fqn"],
            "tgt_type": node["type"],
            "edge_type": "DEFINES",
        }
        for node in nodes
        if node["type"] in ("Function", "Class")
    ]
    if defines_batch:
        gdb.batch_upsert_edges(defines_batch)

    if edges:
        call_batch = [
            {
                "src_fqn": edge["source_id"],
                "src_type": "Function",
                "tgt_fqn": edge["target_id"],
                "tgt_type": "Function",
                "edge_type": "CALLS",
            }
            for edge in edges
        ]
        gdb.batch_upsert_edges(call_batch)
