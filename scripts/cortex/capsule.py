"""
AI 응답에 최적화된 코드 캡슐(Pivot + Skeleton)을 생성하는 모듈.
하이브리드 검색(FTS + sqlite-vec + Graph-RAG) 및 토큰 예산 관리 기능을 포함합니다.
"""

from cortex.db import get_connection, search_nodes_fts
from cortex.skeleton import get_node_skeleton

def generate_context_capsule(workspace_path, query, token_budget=4000, category=None):
    conn = get_connection(workspace_path)
    
    # category=SKILL이면 memories 테이블(스킬 DB)에서 검색
    if category and category.upper() == "SKILL":
        from cortex.persistent_memory import PersistentMemoryManager
        pm = PersistentMemoryManager(workspace_path)
        skill_results = pm.search_knowledge(query, category="skill", limit=5)
        if not skill_results:
            conn.close()
            return "No relevant context found."
        lines = ["=== SKILL CAPSULE ==="]
        for s in skill_results:
            lines.append(f"[{s['key']}] {s.get('content', '')[:300]}")
        conn.close()
        return "\n".join(lines) + "\n=== END OF CAPSULE ==="

    # 1. 소스코드 하이브리드 검색 (vec_nodes + FTS)
    vec_rowids = []
    try:
        from cortex import vector_engine as ve
        from cortex.vectorizer import detect_gpu
        query_vec = ve.get_embeddings([query], use_gpu=detect_gpu())[0]
        vec_query = "SELECT rowid FROM vec_nodes WHERE embedding MATCH ? AND k = 10"
        vec_rows = conn.execute(vec_query, (query_vec.tobytes(),)).fetchall()
        vec_rowids = [r[0] for r in vec_rows]
    except Exception as e:
        import sys
        sys.stderr.write(f"[capsule] vector search err: {e}\n")

    fts_results = search_nodes_fts(conn, query, category=category, limit=5)
    
    results = {r["id"]: dict(r) for r in fts_results}
    if vec_rowids:
        ph = ",".join(["?"] * len(vec_rowids))
        query_nodes = conn.execute(f"SELECT * FROM nodes WHERE rowid IN ({ph})", tuple(vec_rowids)).fetchall()
        for r in query_nodes:
            d = dict(r)
            results[d["id"]] = d
        
    pivots = list(results.values())[:3]
    if not pivots:
        conn.close()
        return "No relevant context found."

    capsule_text = "=== CONTEXT CAPSULE (Graph-RAG) ===\n\n"
    current_tokens = 0
    
    try:
        from cortex.graph_db import GraphDB
        gdb = GraphDB(workspace_path)
    except Exception:
        gdb = None

    for node in pivots:
        fqn = node["fqn"]
        file_path = node["file_path"]
        body = node.get("raw_body") or "Code not available."
        
        node_header = f"--- PIVOT: {fqn} ({file_path}) ---\n"
        if current_tokens + len(body) / 4 > token_budget * 0.7:
             body = get_node_skeleton(node, detail="standard")
             node_header = f"--- SUPPORTING (Budget Limit): {fqn} ({file_path}) ---\n"
        
        content = f"{node_header}{body}\n\n"
        capsule_text += content
        current_tokens += len(content) / 4
        
        if gdb:
            try:
                # Kuzu 1 Depth
                res = gdb.execute("MATCH (a {fqn: $fqn})-[:Calls|Contains]-(b) RETURN b.fqn AS fqn", {"fqn": fqn})
                related_fqns = []
                while res.has_next():
                    related_fqns.append(res.get_next()[0])
                
                for sfqn in related_fqns:
                    if current_tokens > token_budget: break
                    snode = conn.execute("SELECT * FROM nodes WHERE fqn = ?", (sfqn,)).fetchone()
                    if snode:
                        snode = dict(snode)
                        skel = get_node_skeleton(snode, detail="minimal")
                        s_content = f"  - Related (Graph-RAG): {sfqn} (Skeleton: {skel})\n"
                        capsule_text += s_content
                        current_tokens += len(s_content) / 4
            except Exception as e:
                import sys
                sys.stderr.write(f"[capsule] kuzu graph err: {e}\n")

    conn.close()
    capsule_text += "\n=== END OF CAPSULE ==="
    return capsule_text
