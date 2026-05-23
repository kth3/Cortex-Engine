"""Capsule-oriented MCP search handlers."""
from __future__ import annotations

from cortex import storage as pc_db
from cortex.capsules import context as pc_capsule_mod
from cortex.embeddings import provider as ve
from cortex.memories import working as pc_mem_mod
from cortex.skeletons import generator as pc_skeleton_mod

from .search_graph_impact import (
    DEFAULT_IMPACT_DIRECTION,
    DEFAULT_IMPACT_MAX_DEPTH,
    call_get_impact_graph,
)

DEFAULT_SKELETON_DETAIL = "standard"
DEFAULT_CAPSULE_TOKEN_BUDGET = 4000
AUTO_CHAIN_SHORT_CAPSULE_CHARS = 1500
AUTO_CHAIN_IMPACT_DEPTH = 2
AUTO_CHAIN_IMPACT_LIMIT = 10
AUTO_CHAIN_MEMORY_LIMIT = 3


def call_get_file_outline(ctx, args):
    return pc_skeleton_mod.generate_skeleton(
        ctx.workspace,
        args["file_path"],
        args.get("detail", DEFAULT_SKELETON_DETAIL),
    )


def _chain_impact_for_query(ctx, query):
    conn = pc_db.get_connection(ctx.workspace)
    try:
        first_match = pc_db.search_nodes_fts(conn, query, limit=1)
        if not first_match:
            return None
        impact = call_get_impact_graph(
            ctx,
            {
                "fqn": first_match[0]["fqn"],
                "direction": DEFAULT_IMPACT_DIRECTION,
                "max_depth": AUTO_CHAIN_IMPACT_DEPTH,
            },
        )
        return impact.get("impact_nodes", [])[:AUTO_CHAIN_IMPACT_LIMIT]
    finally:
        conn.close()


def _chain_memories_for_query(ctx, query):
    if hasattr(pc_mem_mod, "search_memory"):
        return pc_mem_mod.search_memory(
            ctx.workspace,
            query,
            limit=AUTO_CHAIN_MEMORY_LIMIT,
        )
    return None


def _save_auto_explored_observation(ctx, query) -> None:
    try:
        pc_mem_mod.save_observation(
            ctx.workspace,
            ctx.session_id,
            "insight",
            f"Auto-explored: {query}",
            [],
        )
    except Exception:
        pass


def call_capsule(ctx, args):
    """내부 캡슐 생성 진입점. auto_chain=true 시 통합 탐색 부수효과를 함께 수행한다."""
    query = args["query"]
    auto_chain = args.get("auto_chain", False)
    token_budget = args.get("token_budget", DEFAULT_CAPSULE_TOKEN_BUDGET)

    capsule_str = pc_capsule_mod.generate_context_capsule(ctx.workspace, query, token_budget=token_budget)
    chars = len(capsule_str)
    result = {
        "capsule": capsule_str,
        "chars_used": chars,
        "tokens_estimated": chars // 4,
        "token_budget": token_budget,
    }

    if not auto_chain:
        return result

    if chars < AUTO_CHAIN_SHORT_CAPSULE_CHARS:
        result["reasoning"] = f"Generated capsule was relatively short ({chars} chars). Autonomously chaining impact graph and memories..."
        chained_impact = _chain_impact_for_query(ctx, query)
        if chained_impact is not None:
            result["chained_impact"] = chained_impact

        chained_memories = _chain_memories_for_query(ctx, query)
        if chained_memories is not None:
            result["chained_memories"] = chained_memories
    else:
        result["reasoning"] = f"Generated capsule is robust ({chars} chars). No further chaining required."

    _save_auto_explored_observation(ctx, query)

    return result


def call_search_context(ctx, args):
    """search_context: read-only capsule search. auto_chain side-effect is permanently disabled."""
    return call_capsule(ctx, {**args, "auto_chain": False})
