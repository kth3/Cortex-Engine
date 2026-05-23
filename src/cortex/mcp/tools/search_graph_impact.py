"""Graph-oriented impact MCP handlers."""
from __future__ import annotations

from cortex import storage as pc_db

DEFAULT_IMPACT_DIRECTION = "both"
DEFAULT_IMPACT_MAX_DEPTH = 2
DEFAULT_IMPACT_MAX_NODES = 50


def _impact_neighbors(conn, node_id, direction):
    neighbors = []
    if direction in ["callers", "both"]:
        neighbors.extend(pc_db.get_callers(conn, node_id))
    if direction in ["callees", "both"]:
        neighbors.extend(pc_db.get_callees(conn, node_id))
    return neighbors


def _impact_result(fqn, impact_nodes, truncated, limit, total_seen):
    returned = [n["fqn"] for n in impact_nodes.values()]
    return {
        "fqn": fqn,
        "impact_nodes": returned,
        "truncated": truncated,
        "limit": limit,
        "returned_count": len(returned),
        "total_seen": total_seen,
    }


def call_get_impact_graph(ctx, args):
    fqn = args["fqn"]
    direction = args.get("direction", DEFAULT_IMPACT_DIRECTION)
    max_depth = args.get("max_depth", DEFAULT_IMPACT_MAX_DEPTH)
    max_nodes = args.get("max_nodes", DEFAULT_IMPACT_MAX_NODES)
    conn = pc_db.get_connection(ctx.workspace)
    try:
        node = pc_db.get_node_by_fqn(conn, fqn)
        if not node:
            return {"error": f"Symbol not found: {fqn}"}
        visited = set()
        queue = [(node, 0)]
        impact_nodes = {node["id"]: node}
        total_seen = 1
        truncated = False
        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_depth or curr["id"] in visited:
                continue
            visited.add(curr["id"])
            neighbors = _impact_neighbors(conn, curr["id"], direction)
            for nb in neighbors:
                if nb["id"] in impact_nodes:
                    continue
                total_seen += 1
                if len(impact_nodes) >= max_nodes:
                    truncated = True
                    continue
                impact_nodes[nb["id"]] = nb
                queue.append((nb, depth + 1))
        return _impact_result(fqn, impact_nodes, truncated, max_nodes, total_seen)
    finally:
        conn.close()
