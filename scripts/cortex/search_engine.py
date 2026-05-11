"""Compatibility wrapper for retrieval search."""
from cortex.retrieval.hybrid import hybrid_search, unified_pipeline_search
from cortex.retrieval.fts import _fts_search
from cortex.retrieval.semantic import _vector_search
from cortex.retrieval.ranking import _heuristic_boost

__all__ = [
    "hybrid_search",
    "unified_pipeline_search",
    "_fts_search",
    "_vector_search",
    "_heuristic_boost",
]
