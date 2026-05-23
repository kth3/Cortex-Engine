"""Deep-context pipeline search handlers."""
from __future__ import annotations

from cortex.capsules import context as pc_capsule_mod
from cortex.embeddings import provider as ve
from cortex.retrieval.hybrid import unified_pipeline_search

from .search_capsule import _chain_memories_for_query
from .search_graph_impact import DEFAULT_IMPACT_DIRECTION, call_get_impact_graph

DEFAULT_PIPELINE_LIMIT = 5
PIPELINE_PROBE_EXTRA = 1
PIPELINE_IMPACT_DEPTH = 2
PIPELINE_IMPACT_LIMIT = 10
DEEP_CONTEXT_SPARSE_CAPSULE_CHARS = 1500
DEEP_CONTEXT_CHAIN_MEMORY_LIMIT = 3


def _top_code_fqn(unified_results):
    for result in unified_results:
        if result["domain"] == "code":
            return result.get("key")
    return None


def _pipeline_impact_summary(ctx, unified_results):
    fqn = _top_code_fqn(unified_results)
    if not fqn:
        return []
    impact_res = call_get_impact_graph(
        ctx,
        {
            "fqn": fqn,
            "direction": DEFAULT_IMPACT_DIRECTION,
            "max_depth": PIPELINE_IMPACT_DEPTH,
        },
    )
    return impact_res.get("impact_nodes", [])[:PIPELINE_IMPACT_LIMIT]


def call_search_deep_context(ctx, args):
    query = args["query"]
    limit = args.get("limit", DEFAULT_PIPELINE_LIMIT)
    try:
        probe_limit = limit + PIPELINE_PROBE_EXTRA
        unified_full = unified_pipeline_search(ctx.workspace, query, limit=probe_limit, ve_module=ve)
        truncated = len(unified_full) > limit
        unified = unified_full[:limit]
        total_seen = len(unified_full)

        impact = _pipeline_impact_summary(ctx, unified)
        capsule = pc_capsule_mod.generate_context_capsule(ctx.workspace, query)
        capsule_chars = len(capsule)

        result = {
            "unified_context": unified,
            "capsule": capsule,
            "capsule_chars": capsule_chars,
            "impact_summary": impact,
            "truncated": truncated,
            "limit": limit,
            "returned_count": len(unified),
            "total_seen": total_seen,
        }

        if capsule_chars < DEEP_CONTEXT_SPARSE_CAPSULE_CHARS:
            result["reasoning"] = (
                f"Capsule was sparse ({capsule_chars} chars); "
                "chained additional memories for broader context."
            )
            chained = _chain_memories_for_query(ctx, query)
            if chained is not None:
                result["chained_memories"] = chained

        return result
    except Exception as e:
        return {"error": str(e)}
