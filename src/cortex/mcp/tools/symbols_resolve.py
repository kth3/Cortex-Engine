"""resolve_symbol — 심볼 이름을 FQN 후보로 해석하는 MCP 핸들러."""
from __future__ import annotations

from cortex import storage as pc_db

DEFAULT_LIMIT = 5
FTS_PROBE_MULTIPLIER = 3
VEC_PROBE_MULTIPLIER = 2


def _symbol_candidate(node: dict, match_reason: str) -> dict:
    return {
        "fqn": node["fqn"],
        "name": node["name"],
        "kind": node.get("type", "unknown"),
        "language": node.get("language", "unknown"),
        "file_path": node.get("file_path"),
        "line": node.get("start_line"),
        "match_reason": match_reason,
    }


def _vector_search_nodes(conn, query_name: str, limit: int) -> list:
    from cortex.embeddings import provider as ve
    from cortex.retrieval.queries import VECTOR_NODE_ROWIDS, select_nodes_by_rowids

    try:
        query_vecs = ve.get_embeddings([query_name])
        if query_vecs is None or len(query_vecs) == 0:
            return []

        query_bytes = query_vecs[0].tobytes()
        rowid_rows = conn.execute(VECTOR_NODE_ROWIDS, (query_bytes, limit)).fetchall()
        if not rowid_rows:
            return []

        rowids = [r[0] for r in rowid_rows]
        sql = select_nodes_by_rowids(len(rowids))
        rows = conn.execute(sql, rowids).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def call_resolve_symbol(ctx, args):
    name = args["name"]
    filter_file = args.get("file_path")
    filter_lang = args.get("language")
    limit = args.get("limit", DEFAULT_LIMIT)

    conn = pc_db.get_connection(ctx.workspace)
    try:
        seen_fqns: set = set()
        candidates: list = []

        exact = pc_db.get_node_by_fqn(conn, name)
        if exact:
            candidates.append(_symbol_candidate(exact, "exact_fqn"))
            seen_fqns.add(exact["fqn"])

        if len(candidates) < limit:
            fts_hits = pc_db.search_nodes_fts(conn, name, limit=limit * FTS_PROBE_MULTIPLIER)
            for node in fts_hits:
                if node["fqn"] in seen_fqns:
                    continue
                if filter_file and node.get("file_path") != filter_file:
                    continue
                if filter_lang and node.get("language") != filter_lang:
                    continue
                candidates.append(_symbol_candidate(node, "fts_match"))
                seen_fqns.add(node["fqn"])
                if len(candidates) >= limit:
                    break

        if len(candidates) < limit:
            vec_hits = _vector_search_nodes(conn, name, limit * VEC_PROBE_MULTIPLIER)
            for node in vec_hits:
                if node["fqn"] in seen_fqns:
                    continue
                if filter_file and node.get("file_path") != filter_file:
                    continue
                if filter_lang and node.get("language") != filter_lang:
                    continue
                candidates.append(_symbol_candidate(node, "vector_match"))
                seen_fqns.add(node["fqn"])
                if len(candidates) >= limit:
                    break

        if not candidates:
            return {
                "candidates": [],
                "count": 0,
                "next_suggestion": "try search_context with a broader query",
            }

        return {"candidates": candidates, "count": len(candidates)}
    finally:
        conn.close()
