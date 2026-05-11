"""Indexing pipeline package.

This package hosts the workspace indexing implementation split by responsibility.
The legacy ``cortex.indexer`` module remains the compatibility entrypoint while
call sites are migrated.
"""

from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.cleanup import cleanup_deleted_files, cleanup_file_records
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.graph_sync import sync_file_graph
from cortex.indexing.records import build_node_rows, insert_edges, insert_nodes, upsert_file_cache
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.indexing.vector_store import dedupe_vector_items, persist_node_vectors

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "build_node_rows",
    "cleanup_deleted_files",
    "cleanup_file_records",
    "dedupe_vector_items",
    "insert_edges",
    "insert_nodes",
    "persist_node_vectors",
    "resolve_unresolved_edges",
    "sync_file_graph",
    "sync_rules_to_memories",
    "upsert_file_cache",
]
