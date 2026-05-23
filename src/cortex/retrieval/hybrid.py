"""Hybrid search orchestration.

- 책임: FTS(키워드)와 Semantic(벡터) 검색 결과를 RRF(Reciprocal Rank Fusion) 알고리즘으로 병합하여 최종 순위를 결정한다.
- 주의: limit, multiplier, fallback 논리 및 RRF ranking 알고리즘 정책을 변경하면 최종 검색 결과 순서가 크게 달라질 수 있으므로 임의 수정하지 않는다.
- 예외 정책: 임베딩 실패(서버 오프라인 등) 시 시스템이 죽지 않고 FTS Fallback 검색으로 조용히 전환되도록 예외 처리가 구성되어 있다.
"""
from cortex.storage import get_connection
from cortex.logger import get_logger
from cortex.config.tuning import get_tuning_params
from cortex.retrieval.constants import DEFAULT_LIMIT
from cortex.retrieval.fts import _fts_search
from cortex.retrieval.semantic import _vector_search
from cortex.retrieval.ranking import _heuristic_boost
from cortex.retrieval.queries import (
    OBSERVATIONS_LIKE_RECENT,
    VECTOR_MEMORY_ROWIDS,
    VECTOR_NODE_ROWIDS,
    select_memories_by_rowids,
    select_nodes_by_rowids,
)
from cortex.retrieval.snippets import result_snippet

log = get_logger("search_engine")

KEEP_FIELDS = {"key", "category", "tags", "content", "_score_detail", "_total_score"}


def _sanitize_query(query: str) -> str:
    # sqlite3와 vector engine은 lone surrogate가 포함된 문자열을 인코딩할 때 실패한다.
    try:
        return query.encode('utf-8', 'replace').decode('utf-8')
    except Exception:
        return query


class _RRFAccumulator:
    def __init__(self, rrf_k: int):
        self._rrf_k = rrf_k
        self.scores: dict[str, float] = {}
        self.details: dict[str, tuple[str, dict]] = {}

    def add(self, domain: str, items, id_field: str) -> None:
        for i, item in enumerate(items):
            key = f"{domain}:{item[id_field]}"
            self.scores[key] = self.scores.get(key, 0.0) + 1.0 / (i + self._rrf_k)
            self.details[key] = (domain, item)

    def top(self, limit: int, boost_fn) -> list[tuple]:
        boost_cache = {k: boost_fn(domain, item) for k, (domain, item) in self.details.items()}
        ordered = sorted(
            self.scores.keys(),
            key=lambda k: self.scores[k] + boost_cache[k],
            reverse=True,
        )[:limit]
        return [(k, self.details[k][0], self.details[k][1], self.scores[k], boost_cache[k]) for k in ordered]


def _format_code_result(item, base_score, boost, snippet_len) -> dict:
    return {
        "domain": "code",
        "key": item.get("fqn", ""),
        "category": item.get("type", "unknown"),
        "file_path": item.get("file_path", ""),
        "snippet": result_snippet(item, domain="code", max_chars=snippet_len),
        "_total_score": round(base_score + boost, 6),
    }


def _format_knowledge_result(item, base_score, boost, snippet_len) -> dict:
    return {
        "domain": "knowledge",
        "key": item.get("key", ""),
        "category": item.get("category", "unknown"),
        "snippet": result_snippet(item, domain="knowledge", max_chars=snippet_len),
        "_total_score": round(base_score + boost, 6),
    }


def _format_observation_result(item, base_score, boost, snippet_len) -> dict:
    return {
        "domain": "observation",
        "key": str(item.get("id", "")),
        "category": item.get("type", "observation"),
        "snippet": result_snippet(item, domain="observation", max_chars=snippet_len),
        "_total_score": round(base_score + boost, 6),
    }


_FORMATTERS = {
    "code": _format_code_result,
    "knowledge": _format_knowledge_result,
    "observation": _format_observation_result,
}


def hybrid_search(workspace: str, query: str, category: str = None, limit: int = DEFAULT_LIMIT, ve_module=None) -> list:
    """영구 지식 및 전문가 스킬 하이브리드 검색 (FTS5 + sqlite-vec + RRF 스코어링)

    Args:
        workspace: 워크스페이스 경로
        query: 검색 쿼리
        category: 필터링 카테고리 (선택)
        limit: 최대 결과 수
        ve_module: vector_engine 모듈 (None이면 벡터 검색 생략)
    Returns:
        정렬된 결과 리스트 (key, category, content snippet, score)
    """
    params = get_tuning_params(workspace)
    snippet_len = params["search_snippet_len"]
    multiplier = params["search_multiplier"]
    rrf_k = params["rrf_k"]

    query = _sanitize_query(query)

    # category 대소문자 정규화 ('SKILL' → 'skill')
    if category:
        category = category.lower()

    # 1. FTS5 + Vector 독립 검색
    fts_results = _fts_search(workspace, query, category, limit, multiplier)
    vec_results = _vector_search(workspace, query, category, limit, multiplier, ve_module)

    # 2. RRF 점수 병합
    vec_map = {vr["id"]: vr for vr in vec_results}
    fts_rrf = {r["key"]: 1.0 / (i + rrf_k) for i, r in enumerate(fts_results)}
    vec_rrf = {vr["id"]: 1.0 / (i + rrf_k) for i, vr in enumerate(vec_results)}

    item_info: dict[str, str] = {}
    for r in fts_results:
        item_info[r["key"]] = r.get("category", "unknown")
    for k, v in vec_map.items():
        if k not in item_info:
            item_info[k] = v.get("meta", {}).get("category", "skill")

    all_keys = set(fts_rrf.keys()) | set(vec_map.keys())
    boost_cache = {k: _heuristic_boost(k, item_info.get(k, ""), query) for k in all_keys}
    combined = sorted(
        all_keys,
        key=lambda k: fts_rrf.get(k, 0.0) + vec_rrf.get(k, 0.0) + boost_cache[k],
        reverse=True,
    )[:limit]

    # 3. 결과 생성 (토큰 절약: content snippet + 필수 필드만)
    fts_result_map = {r["key"]: r for r in fts_results}
    final: list[dict] = []
    for k in combined:
        boost_val = boost_cache[k]
        rrf_val = fts_rrf.get(k, 0.0) + vec_rrf.get(k, 0.0)
        score_detail = {"rrf": round(rrf_val, 6), "boost": round(boost_val, 6)}
        total = round(rrf_val + boost_val, 6)
        if k in fts_result_map:
            raw = fts_result_map[k]
            item = {f: raw[f] for f in KEEP_FIELDS if f in raw}
            if "content" in item:
                item["content"] = result_snippet(item, domain="knowledge", max_chars=snippet_len)
            item["_score_detail"] = score_detail
            item["_total_score"] = total
            final.append(item)
        elif k in vec_map:
            final.append({
                "key": k,
                "content": result_snippet({"content": vec_map[k].get("text", "")}, domain="knowledge", max_chars=snippet_len),
                "category": item_info.get(k, "skill"),
                "_score_detail": score_detail,
                "_total_score": total,
            })
    return final


def _run_vector_searches(conn, query: str, limit: int, multiplier: int, ve_module) -> tuple[list, list]:
    code_results: list[dict] = []
    knowledge_results: list[dict] = []
    if ve_module is None:
        return code_results, knowledge_results

    try:
        from cortex.embeddings.hardware import detect_gpu
        query_vec = ve_module.get_embeddings([query], use_gpu=detect_gpu())[0]

        vec_nodes_rows = conn.execute(
            VECTOR_NODE_ROWIDS,
            (query_vec.tobytes(), limit * multiplier),
        ).fetchall()
        if vec_nodes_rows:
            rowids = [r[0] for r in vec_nodes_rows]
            db_nodes = conn.execute(
                select_nodes_by_rowids(len(rowids)), rowids,
            ).fetchall()
            n_map = {r["rowid"]: dict(r) for r in db_nodes}
            for rowid in rowids:
                if rowid in n_map:
                    code_results.append(n_map[rowid])

        vec_mem_rows = conn.execute(
            VECTOR_MEMORY_ROWIDS,
            (query_vec.tobytes(), limit * multiplier),
        ).fetchall()
        if vec_mem_rows:
            rowids = [r[0] for r in vec_mem_rows]
            db_mems = conn.execute(
                select_memories_by_rowids(len(rowids), include_rowid=True), rowids,
            ).fetchall()
            m_map = {r["rowid"]: dict(r) for r in db_mems}
            for rowid in rowids:
                if rowid in m_map:
                    knowledge_results.append(m_map[rowid])
    except Exception as e:
        log.error("Unified vector search failed: %s", e)

    return code_results, knowledge_results


def unified_pipeline_search(workspace: str, query: str, limit: int = DEFAULT_LIMIT, ve_module=None) -> list:
    """
    코드(vec_nodes) + 지식(vec_memories) + 동적메모리(observations FTS/LIKE)를
    단일 임베딩으로 교차 RRF 검색.
    """
    params = get_tuning_params(workspace)
    snippet_len = params["search_snippet_len"]
    multiplier = params["search_multiplier"]
    rrf_k = params["rrf_k"]

    query = _sanitize_query(query)

    conn = get_connection(workspace)

    # 1. 단일 임베딩 및 벡터 검색
    code_results, knowledge_results = _run_vector_searches(conn, query, limit, multiplier, ve_module)

    # 2. 동적 메모리 (observations LIKE)
    obs_results: list[dict] = []
    try:
        obs_rows = conn.execute(
            OBSERVATIONS_LIKE_RECENT,
            (f"%{query}%", limit * multiplier),
        ).fetchall()
        for r in obs_rows:
            obs_results.append(dict(r))
    except Exception as e:
        log.error("Observation search failed: %s", e)

    # 3. RRF 병합
    rrf = _RRFAccumulator(rrf_k)
    rrf.add("code", code_results, "fqn")
    rrf.add("knowledge", knowledge_results, "key")
    rrf.add("observation", obs_results, "id")

    # 4. FTS Fallback (임베딩 실패 시 또는 결과 보완용)
    # _fts_search는 내부에서 독립 conn을 열므로 여기서는 별도 호출 유지
    fts_mems = _fts_search(workspace, query, limit=limit, multiplier=multiplier)
    rrf.add("knowledge", fts_mems, "key")

    # 코드 FTS도 이미 열린 conn을 재사용하여 커넥션 낭비 방지
    try:
        from cortex.storage import search_nodes_fts
        fts_nodes = search_nodes_fts(conn, query, limit=limit)
        rrf.add("code", fts_nodes, "fqn")
    except Exception:
        pass
    finally:
        conn.close()

    # 5. 정렬 및 결과 포맷팅
    def _boost(domain: str, item: dict) -> float:
        if domain != "knowledge":
            return 0.0
        return _heuristic_boost(item.get("key", ""), item.get("category", ""), query)

    final: list[dict] = []
    for _key, domain, item, base_score, boost in rrf.top(limit, _boost):
        final.append(_FORMATTERS[domain](item, base_score, boost, snippet_len))
    return final
