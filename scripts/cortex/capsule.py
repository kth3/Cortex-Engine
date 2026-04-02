"""
AI 응답에 최적화된 코드 캡슐(Pivot + Skeleton)을 생성하는 모듈.
하이브리드 검색(FTS + Graph Centrality) 및 토큰 예산 관리 기능을 포함합니다.
"""

# 패키지 모듈 임포트
from cortex.db import get_connection, search_nodes_fts, get_node_by_id
from cortex.skeleton import get_node_skeleton
from cortex.impact import get_impact_tree

def generate_context_capsule(workspace_path, query, token_budget=8000, category=None):
    """
    하이브리드 검색 기반 Context Capsule 생성
    - query: 검색어
    - token_budget: 대략적인 토큰 한도 (글자 수 / 4 로 계산)
    """
    conn = get_connection(workspace_path)
    
    # 1. 하이브리드 검색 (FTS5 검색 결과)
    results = search_nodes_fts(conn, query, category=category, limit=5)
    if not results:
        conn.close()
        return "No relevant context found."
        
    capsule_text = "=== CONTEXT CAPSULE (Cortex) ===\n\n"
    current_tokens = 0
    
    # 2. 결과 랭킹 (FTS 결과에서 상위 노드들 처리)
    # Pivot 노드: 검색 결과의 상위 3개까지는 전문을 포함 시도
    pivots = results[:3]
    
    # 3. 캡슐 조립
    for node in pivots:
        node_id = node["id"]
        fqn = node["fqn"]
        file_path = node["file_path"]
        body = node.get("raw_body", "Code not available.")
        
        node_header = f"--- PIVOT: {fqn} ({file_path}) ---\n"
        
        # 토큰 예산 체크
        if current_tokens + len(body) / 4 > token_budget * 0.7:
             # 예산이 부족해지면 피벗 노드도 스켈레톤으로 전환
             body = get_node_skeleton(node, detail="standard")
             node_header = f"--- SUPPORTING (Budget Limit): {fqn} ({file_path}) ---\n"
        
        content = f"{node_header}{body}\n\n"
        capsule_text += content
        current_tokens += len(content) / 4
        
        # 4. 연관 노드 (Supporting Nodes: Callers/Callees)
        # 각 피벗 노드의 주변(1단계) 호출 관계 노드들은 스켈레톤만 추가
        impact = get_impact_tree(conn, node_id, direction='both', max_depth=1)
        supporting_nodes = impact["nodes"]
        
        for sid, snode in supporting_nodes.items():
            if sid == node_id: continue # 자신은 제외
            if current_tokens > token_budget: break
            
            sfqn = snode["fqn"]
            sfile = snode["file_path"]
            # 스켈레톤 추출을 위해 노드 정보를 다시 읽음 (raw_body 필요)
            full_snode = get_node_by_id(conn, sid)
            # full_snode가 없을 경우 snode를 그대로 시그니처만 사용
            skel = get_node_skeleton(full_snode if full_snode else snode, detail="minimal")
            
            s_content = f"  - Related: {sfqn} (Skeleton: {skel})\n"
            capsule_text += s_content
            current_tokens += len(s_content) / 4

    conn.close()
    capsule_text += "\n=== END OF CAPSULE ==="
    return capsule_text
