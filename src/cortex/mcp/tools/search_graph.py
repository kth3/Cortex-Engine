"""Graph-oriented MCP search handlers."""
from __future__ import annotations

from cortex import storage as pc_db

DEFAULT_IMPACT_DIRECTION = "both"
DEFAULT_IMPACT_MAX_DEPTH = 2
DEFAULT_IMPACT_MAX_NODES = 50
DEFAULT_LOGIC_MAX_DEPTH = 6
DEFAULT_LOGIC_MAX_NODES = 200


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


def _logic_flow_result(path, truncated, limit, total_seen, returned_count=None):
    return {
        "path": path,
        "truncated": truncated,
        "limit": limit,
        "returned_count": len(path) if returned_count is None else returned_count,
        "total_seen": total_seen,
    }


def _node_fqns(conn, node_ids):
    path_nodes = [pc_db.get_node_by_id(conn, pid) for pid in node_ids]
    return [n["fqn"] for n in path_nodes]


def call_find_execution_path(ctx, args):
    from_fqn = args["from_fqn"]
    to_fqn = args["to_fqn"]
    max_depth = args.get("max_depth", DEFAULT_LOGIC_MAX_DEPTH)
    max_nodes = args.get("max_nodes", DEFAULT_LOGIC_MAX_NODES)
    conn = pc_db.get_connection(ctx.workspace)
    try:
        start_node = pc_db.get_node_by_fqn(conn, from_fqn)
        end_node = pc_db.get_node_by_fqn(conn, to_fqn)
        if not start_node or not end_node:
            return {"error": "Start or end symbol not found."}
        queue = [[start_node["id"]]]
        visited = set()
        total_seen = 1
        truncated = False
        while queue:
            path = queue.pop(0)
            curr = path[-1]
            if curr == end_node["id"]:
                returned = _node_fqns(conn, path)
                return _logic_flow_result(returned, truncated=False, limit=max_nodes, total_seen=total_seen)
            if len(path) - 1 >= max_depth:
                truncated = True
                continue
            if curr in visited:
                continue
            visited.add(curr)
            if len(visited) >= max_nodes:
                truncated = True
                continue
            callees = pc_db.get_callees(conn, curr)
            for callee in callees:
                total_seen += 1
                queue.append(path + [callee["id"]])

        return _logic_flow_result([], truncated=truncated, limit=max_nodes, total_seen=total_seen, returned_count=0)
    finally:
        conn.close()

