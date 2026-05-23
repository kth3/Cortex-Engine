"""resolve_symbol — 심볼 이름을 FQN 후보로 해석하는 MCP 핸들러.

검색 우선순위:
  1. 정확한 FQN 일치 (DB 직접 조회)
  2. FTS 키워드 검색 (name / fqn / docstring / signature 대상)
  3. 벡터 유사도 검색 (임베딩 서버/모델 사용 가능한 경우에만)

3단계는 서버 미실행 혹은 모델 미설치 환경에서 graceful fallback(빈 목록 반환).
"""
from cortex import storage as pc_db

DEFAULT_LIMIT = 5
# FTS 검색 시 실제 limit보다 많이 조회한 뒤 필터링
FTS_PROBE_MULTIPLIER = 3
# 벡터 검색 시 실제 limit보다 많이 조회한 뒤 필터링
VEC_PROBE_MULTIPLIER = 2


def _symbol_candidate(node: dict, match_reason: str) -> dict:
    """노드 dict를 에이전트 친화적 후보 포맷으로 변환한다."""
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
    """벡터 유사도 검색으로 노드 후보를 찾는다.

    임베딩 서버가 오프라인이거나 vec_nodes 테이블이 없으면 빈 목록을 반환한다.
    vec_nodes에서 rowid로 조회하는 2-step 구조는 sqlite-vec의 ANN 쿼리 제약 때문이다.
    """
    from cortex.embeddings import provider as ve
    from cortex.retrieval.queries import VECTOR_NODE_ROWIDS, select_nodes_by_rowids

    try:
        # 쿼리 이름을 1024차원 float32 벡터로 변환
        query_vecs = ve.get_embeddings([query_name])
        if query_vecs is None or len(query_vecs) == 0:
            return []

        # sqlite-vec는 bytes 형태로 쿼리 벡터를 받는다
        query_bytes = query_vecs[0].tobytes()
        rowid_rows = conn.execute(VECTOR_NODE_ROWIDS, (query_bytes, limit)).fetchall()
        if not rowid_rows:
            return []

        # rowid → 실제 노드 레코드 조회
        rowids = [r[0] for r in rowid_rows]
        sql = select_nodes_by_rowids(len(rowids))
        rows = conn.execute(sql, rowids).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # 임베딩 불가 환경에서 예외 흡수 후 graceful fallback
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

        # 1단계: 정확한 FQN 일치 — 가장 신뢰도 높은 결과
        exact = pc_db.get_node_by_fqn(conn, name)
        if exact:
            candidates.append(_symbol_candidate(exact, "exact_fqn"))
            seen_fqns.add(exact["fqn"])

        # 2단계: FTS 키워드 검색 — 부분 이름·docstring 등 텍스트 기반 매칭
        if len(candidates) < limit:
            fts_hits = pc_db.search_nodes_fts(conn, name, limit=limit * FTS_PROBE_MULTIPLIER)
            for node in fts_hits:
                if node["fqn"] in seen_fqns:
                    continue
                # 호출자가 파일/언어를 지정한 경우 해당 범위로 좁힌다
                if filter_file and node.get("file_path") != filter_file:
                    continue
                if filter_lang and node.get("language") != filter_lang:
                    continue
                candidates.append(_symbol_candidate(node, "fts_match"))
                seen_fqns.add(node["fqn"])
                if len(candidates) >= limit:
                    break

        # 3단계: 벡터 유사도 검색 — FTS로 충분한 후보를 얻지 못한 경우에만 실행
        # 임베딩 서버/모델이 없는 환경에서는 빈 목록이 반환되므로 결과에 영향 없음
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

        # 후보가 없을 때: 빈 목록 + 다음 행동 제안을 반환
        if not candidates:
            return {
                "candidates": [],
                "count": 0,
                "next_suggestion": "try search_context with a broader query",
            }

        return {"candidates": candidates, "count": len(candidates)}
    finally:
        conn.close()
