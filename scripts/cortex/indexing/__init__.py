"""Indexing pipeline package.

This package hosts the workspace indexing implementation split by responsibility.
The legacy ``cortex.indexer`` module remains the compatibility entrypoint while
call sites are migrated.
"""

from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.cleanup import cleanup_deleted_files, cleanup_file_records
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.rules_sync import sync_rules_to_memories

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "cleanup_deleted_files",
    "cleanup_file_records",
    "resolve_unresolved_edges",
    "sync_rules_to_memories",
]
