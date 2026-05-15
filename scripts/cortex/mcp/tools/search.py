"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
from cortex import db as pc_db
from cortex import capsule as pc_capsule_mod
from cortex import skeleton as pc_skeleton_mod
from cortex import memory as pc_mem_mod
from cortex.retrieval.hybrid import unified_pipeline_search
from cortex import vector_engine as ve

DEFAULT_SKELETON_DETAIL = "standard"

DEFAULT_IMPACT_DIRECTION = "both"
DEFAULT_IMPACT_MAX_DEPTH = 2
DEFAULT_IMPACT_MAX_NODES = 50

DEFAULT_LOGIC_MAX_DEPTH = 6
DEFAULT_LOGIC_MAX_NODES = 200

DEFAULT_CAPSULE_TOKEN_BUDGET = 4000
AUTO_CHAIN_SHORT_CAPSULE_CHARS = 1500
AUTO_CHAIN_IMPACT_DEPTH = 2
AUTO_CHAIN_IMPACT_LIMIT = 10
AUTO_CHAIN_MEMORY_LIMIT = 3

DEFAULT_PIPELINE_LIMIT = 5
PIPELINE_PROBE_EXTRA = 1
PIPELINE_IMPACT_DEPTH = 2
PIPELINE_IMPACT_LIMIT = 10


def call_pc_skeleton(ctx, args):
    return pc_skeleton_mod.generate_skeleton(
        ctx.workspace,
        args["file_path"],
        args.get("detail", DEFAULT_SKELETON_DETAIL),
    )


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


def call_pc_impact_graph(ctx, args):
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
        total_seen = 1   # 발견된 모든 후보 노드 수 (limit 초과 포함)
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


def call_pc_logic_flow(ctx, args):
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
                return _logic_flow_result(
                    returned,
                    truncated=False,
                    limit=max_nodes,
                    total_seen=total_seen,
                )
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

        return _logic_flow_result(
            [],
            truncated=truncated,
            limit=max_nodes,
            total_seen=total_seen,
            returned_count=0,
        )
    finally:
        conn.close()


def _chain_impact_for_query(ctx, query):
    conn = pc_db.get_connection(ctx.workspace)
    try:
        first_match = pc_db.search_nodes_fts(conn, query, limit=1)
        if not first_match:
            return None
        impact = call_pc_impact_graph(
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
        pass  # observation 기록 실패가 capsule 응답을 차단해서는 안 됨


def call_pc_capsule(ctx, args):
    """pc_capsule 통합 진입점. auto_chain=true 시 통합 탐색 부수효과를 함께 수행한다.

    부수효과 (auto_chain=true 한정):
      1. capsule 길이 < 1500 chars 시 impact_graph + memory 자동 체이닝
      2. save_observation에 'Auto-explored: <query>' 기록
    auto_chain=false (기본) 시: 단순 capsule 생성 + chars/tokens 메타만.
    """
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

    # auto_chain=true 부수효과 — 통합 탐색 흐름 인라인 처리
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


def _top_code_fqn(unified_results):
    for result in unified_results:
        if result["domain"] == "code":
            return result.get("key")
    return None


def _pipeline_impact_summary(ctx, unified_results):
    fqn = _top_code_fqn(unified_results)
    if not fqn:
        return []
    impact_res = call_pc_impact_graph(
        ctx,
        {
            "fqn": fqn,
            "direction": DEFAULT_IMPACT_DIRECTION,
            "max_depth": PIPELINE_IMPACT_DEPTH,
        },
    )
    return impact_res.get("impact_nodes", [])[:PIPELINE_IMPACT_LIMIT]


def call_pc_run_pipeline(ctx, args):
    query = args["query"]
    limit = args.get("limit", DEFAULT_PIPELINE_LIMIT)
    try:
        # 1. 통합 교차 검색 수행 (limit + 1개로 truncated 여부만 추정하고, 실제 반환은 limit개로 제한한다.)
        probe_limit = limit + PIPELINE_PROBE_EXTRA
        unified_full = unified_pipeline_search(ctx.workspace, query, limit=probe_limit, ve_module=ve)
        truncated = len(unified_full) > limit
        unified = unified_full[:limit]
        total_seen = len(unified_full)

        # 2. 코드 도메인 1위 항목 FQN 추출 및 Impact Graph 요약 추출
        impact = _pipeline_impact_summary(ctx, unified)

        # 3. 보완용 상세 코드 캡슐 생성 (Option B)
        capsule = pc_capsule_mod.generate_context_capsule(ctx.workspace, query)

        return {
            "unified_context": unified,
            "capsule": capsule,
            "impact_summary": impact,
            "truncated": truncated,
            "limit": limit,
            "returned_count": len(unified),
            "total_seen": total_seen,
        }
    except Exception as e:
        return {"error": str(e)}
