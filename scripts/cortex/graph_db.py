"""Compatibility wrapper for graph storage."""
from cortex.storage.graph import get_graph_db_path, _kuzu_table, GraphDB

__all__ = [
    "get_graph_db_path",
    "_kuzu_table",
    "GraphDB",
]
