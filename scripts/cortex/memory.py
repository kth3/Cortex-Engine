"""Compatibility wrapper for working memory."""
from cortex.memories.working import (
    save_observation,
    search_memory,
    get_session_context,
)

__all__ = [
    "save_observation",
    "search_memory",
    "get_session_context",
]
