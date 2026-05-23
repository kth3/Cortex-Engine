"""Indexing pipeline package."""

from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.cleanup import cleanup_deleted_files, cleanup_file_records
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.file_pipeline import index_file
from cortex.indexing.graph_sync import sync_file_graph
from cortex.indexing.incremental import incremental_index_changed
from cortex.indexing.records import build_node_rows, insert_edges, insert_nodes, upsert_file_cache
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.indexing.vector_store import dedupe_vector_items, persist_node_vectors
from cortex.indexing.workspace import index_workspace

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "build_node_rows",
    "cleanup_deleted_files",
    "cleanup_file_records",
    "dedupe_vector_items",
    "insert_edges",
    "insert_nodes",
    "incremental_index_changed",
    "index_file",
    "index_workspace",
    "persist_node_vectors",
    "resolve_unresolved_edges",
    "sync_file_graph",
    "sync_rules_to_memories",
    "upsert_file_cache",
]
