from cortex import db as pc_db
from cortex import capsule as pc_capsule_mod
from cortex import skeleton as pc_skeleton_mod
from cortex import memory as pc_mem_mod
from cortex.search_engine import unified_pipeline_search
from cortex import vector_engine as ve

def call_pc_skeleton(ctx, args):
    return pc_skeleton_mod.generate_skeleton(
        ctx.workspace, 
        args["file_path"], 
        args.get("detail", "standard")
    )

def call_pc_impact_graph(ctx, args):
    fqn = args["fqn"]
    direction = args.get("direction", "both")
    max_depth = args.get("max_depth", 2)
    max_nodes = args.get("max_nodes", 50)
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
            neighbors = []
            if direction in ["callers", "both"]:
                neighbors.extend(pc_db.get_callers(conn, curr["id"]))
            if direction in ["callees", "both"]:
                neighbors.extend(pc_db.get_callees(conn, curr["id"]))
            for nb in neighbors:
                if nb["id"] in impact_nodes:
                    continue
                total_seen += 1
                if len(impact_nodes) >= max_nodes:
                    truncated = True
                    continue
                impact_nodes[nb["id"]] = nb
                queue.append((nb, depth + 1))
        returned = [n["fqn"] for n in impact_nodes.values()]
        return {
            "fqn": fqn,
            "impact_nodes": returned,
            "truncated": truncated,
            "limit": max_nodes,
            "returned_count": len(returned),
            "total_seen": total_seen,
        }
    finally:
        conn.close()

def call_pc_logic_flow(ctx, args):
    from_fqn = args["from_fqn"]
    to_fqn = args["to_fqn"]
    max_depth = args.get("max_depth", 6)
    max_nodes = args.get("max_nodes", 200)
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
                path_nodes = [pc_db.get_node_by_id(conn, pid) for pid in path]
                returned = [n["fqn"] for n in path_nodes]
                return {
                    "path": returned,
                    "truncated": False,
                    "limit": max_nodes,
                    "returned_count": len(returned),
                    "total_seen": total_seen,
                }
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
        return {
            "path": [],
            "truncated": truncated,
            "limit": max_nodes,
            "returned_count": 0,
            "total_seen": total_seen,
        }
    finally:
        conn.close()

def call_pc_capsule(ctx, args):
    """pc_capsule 통합 진입점. auto_chain=true 시 통합 탐색 부수효과를 함께 수행한다.

    부수효과 (auto_chain=true 한정):
      1. capsule 길이 < 1500 chars 시 impact_graph + memory 자동 체이닝
      2. save_observation에 'Auto-explored: <query>' 기록
    auto_chain=false (기본) 시: 단순 capsule 생성 + chars/tokens 메타만.
    """
    query = args["query"]
    auto_chain = args.get("auto_chain", False)
    token_budget = args.get("token_budget", 4000)

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
    if chars < 1500:
        result["reasoning"] = f"Generated capsule was relatively short ({chars} chars). Autonomously chaining impact graph and memories..."
        conn = pc_db.get_connection(ctx.workspace)
        try:
            first_match = pc_db.search_nodes_fts(conn, query, limit=1)
            if first_match:
                impact = call_pc_impact_graph(ctx, {"fqn": first_match[0]["fqn"], "direction": "both", "max_depth": 2})
                result["chained_impact"] = impact.get("impact_nodes", [])[:10]
        finally:
            conn.close()

        if hasattr(pc_mem_mod, "search_memory"):
            mem = pc_mem_mod.search_memory(ctx.workspace, query, limit=3)
            result["chained_memories"] = mem
    else:
        result["reasoning"] = f"Generated capsule is robust ({chars} chars). No further chaining required."

    try:
        pc_mem_mod.save_observation(ctx.workspace, ctx.session_id, "insight", f"Auto-explored: {query}", [])
    except Exception:
        pass  # observation 기록 실패가 capsule 응답을 차단해서는 안 됨

    return result

def call_pc_run_pipeline(ctx, args):
    query = args["query"]
    limit = args.get("limit", 5)
    try:
        # 1. 통합 교차 검색 수행 (limit + 1로 truncated 추정)
        probe_limit = limit + 1
        unified_full = unified_pipeline_search(ctx.workspace, query, limit=probe_limit, ve_module=ve)
        truncated = len(unified_full) > limit
        unified = unified_full[:limit]
        total_seen = len(unified_full)

        # 2. 코드 도메인 1위 항목 FQN 추출 및 Impact Graph 스킵 처리
        code_results = [r for r in unified if r["domain"] == "code"]
        impact = []
        if code_results:
            fqn = code_results[0].get("key")
            if fqn:
                impact_res = call_pc_impact_graph(ctx, {"fqn": fqn, "direction": "both", "max_depth": 2})
                impact = impact_res.get("impact_nodes", [])[:10]

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
