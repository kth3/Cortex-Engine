"""Graph-oriented execution path MCP handlers."""
from __future__ import annotations

from cortex import storage as pc_db

DEFAULT_LOGIC_MAX_DEPTH = 6
DEFAULT_LOGIC_MAX_NODES = 200


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
