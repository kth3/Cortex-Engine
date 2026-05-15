"""Hybrid search orchestration.

- 책임: FTS(키워드)와 Semantic(벡터) 검색 결과를 RRF(Reciprocal Rank Fusion) 알고리즘으로 병합하여 최종 순위를 결정한다.
- 주의: limit, multiplier, fallback 논리 및 RRF ranking 알고리즘 정책을 변경하면 최종 검색 결과 순서가 크게 달라질 수 있으므로 임의 수정하지 않는다.
- 예외 정책: 임베딩 실패(서버 오프라인 등) 시 시스템이 죽지 않고 FTS Fallback 검색으로 조용히 전환되도록 예외 처리가 구성되어 있다.
"""
from cortex.db import get_connection
from cortex.logger import get_logger
from cortex.indexer_utils import get_tuning_params
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

log = get_logger("search_engine")

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

    # [BUGFIX] Surrogate 문자열 제거 (sqlite3 및 vector engine 에러 방지)
    try:
        query = query.encode('utf-8', 'replace').decode('utf-8')
    except Exception:
        pass

    # category 대소문자 정규화 ('SKILL' → 'skill')
    if category:
        category = category.lower()

    # 1. FTS5 + Vector 독립 검색
    fts_results = _fts_search(workspace, query, category, limit, multiplier)
    vec_results = _vector_search(workspace, query, category, limit, multiplier, ve_module)

    # 2. RRF 점수 병합
    fts_keys = {r["key"] for r in fts_results}
    vec_map = {vr["id"]: vr for vr in vec_results}
    fts_rrf = {r["key"]: 1.0 / (i + rrf_k) for i, r in enumerate(fts_results)}
    vec_rrf = {vr["id"]: 1.0 / (i + rrf_k) for i, vr in enumerate(vec_results)}

    item_info = {}
    for r in fts_results:
        item_info[r["key"]] = r.get("category", "unknown")
    for k, v in vec_map.items():
        if k not in item_info:
            item_info[k] = v.get("meta", {}).get("category", "skill")

    all_keys = set(fts_keys) | set(vec_map.keys())
    combined = sorted(
        all_keys,
        key=lambda k: fts_rrf.get(k, 0.0) + vec_rrf.get(k, 0.0) + _heuristic_boost(k, item_info.get(k, ""), query),
        reverse=True
    )[:limit]

    # 3. 결과 생성 (토큰 절약: content snippet + 필수 필드만)
    KEEP_FIELDS = {"key", "category", "tags", "content", "_score_detail", "_total_score"}
    fts_result_map = {r["key"]: r for r in fts_results}
    final = []
    for k in combined:
        boost_val = _heuristic_boost(k, item_info.get(k, ""), query)
        rrf_val = fts_rrf.get(k, 0.0) + vec_rrf.get(k, 0.0)
        if k in fts_result_map:
            raw = fts_result_map[k]
            item = {f: raw[f] for f in KEEP_FIELDS if f in raw}
            if "content" in item:
                item["content"] = item["content"][:snippet_len]
            item["_score_detail"] = {"rrf": round(rrf_val, 6), "boost": round(boost_val, 6)}
            item["_total_score"] = round(rrf_val + boost_val, 6)
            final.append(item)
        elif k in vec_map:
            final.append({
                "key": k,
                "content": vec_map[k].get("text", "")[:snippet_len],
                "category": item_info.get(k, "skill"),
                "_score_detail": {"rrf": round(rrf_val, 6), "boost": round(boost_val, 6)},
                "_total_score": round(rrf_val + boost_val, 6)
            })
    return final


MAX_CODE_SNIPPET_CHARS = 400


def _compact_text_preview(text: str | None, max_chars: int = MAX_CODE_SNIPPET_CHARS) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    preview = "\n".join(lines[:6])
    if len(preview) > max_chars:
        return preview[: max_chars - 1].rstrip() + "…"
    return preview


def _code_result_snippet(row: dict) -> str:
    signature = row.get("signature")
    if signature:
        return signature

    for key in ("content", "code", "body", "text", "raw_body"):
        preview = _compact_text_preview(row.get(key))
        if preview:
            return preview

    fqn = row.get("fqn") or row.get("name") or "<unknown>"
    file_path = row.get("file_path") or row.get("path") or "<unknown>"
    line_start = row.get("start_line") or row.get("line_start")
    line_end = row.get("end_line") or row.get("line_end")
    if line_start and line_end:
        return f"{fqn} ({file_path}:{line_start}-{line_end})"
    if line_start:
        return f"{fqn} ({file_path}:{line_start})"
    return f"{fqn} ({file_path})"


def unified_pipeline_search(workspace: str, query: str, limit: int = DEFAULT_LIMIT, ve_module=None) -> list:
    """
    코드(vec_nodes) + 지식(vec_memories) + 동적메모리(observations FTS/LIKE)를
    단일 임베딩으로 교차 RRF 검색.
    """
    params = get_tuning_params(workspace)
    snippet_len = params["search_snippet_len"]
    multiplier = params["search_multiplier"]
    rrf_k = params["rrf_k"]

    # [BUGFIX] Surrogate 문자열 제거 (sqlite3 및 vector engine 에러 방지)
    try:
        query = query.encode('utf-8', 'replace').decode('utf-8')
    except Exception:
        pass

    conn = get_connection(workspace)
    
    code_results = []
    knowledge_results = []
    obs_results = []
    
    # 1. 단일 임베딩 및 벡터 검색
    if ve_module is not None:
        try:
            from cortex.embeddings.hardware import detect_gpu
            query_vec = ve_module.get_embeddings([query], use_gpu=detect_gpu())[0]
            
            # vec_nodes (코드 도메인)
            vec_nodes_rows = conn.execute(
                VECTOR_NODE_ROWIDS,
                (query_vec.tobytes(), limit * multiplier)
            ).fetchall()
            
            if vec_nodes_rows:
                rowids = [r[0] for r in vec_nodes_rows]
                # rowid는 SELECT *에 포함되지 않으므로 명시적으로 선택
                db_nodes = conn.execute(
                    select_nodes_by_rowids(len(rowids)), rowids
                ).fetchall()
                n_map = {r["rowid"]: dict(r) for r in db_nodes}
                for rowid in rowids:
                    if rowid in n_map:
                        code_results.append(n_map[rowid])
            
            # vec_memories (지식 도메인)
            vec_mem_rows = conn.execute(
                VECTOR_MEMORY_ROWIDS,
                (query_vec.tobytes(), limit * multiplier)
            ).fetchall()
            
            if vec_mem_rows:
                rowids = [r[0] for r in vec_mem_rows]
                db_mems = conn.execute(
                    select_memories_by_rowids(len(rowids), include_rowid=True), rowids
                ).fetchall()
                m_map = {r["rowid"]: dict(r) for r in db_mems}
                for rowid in rowids:
                    if rowid in m_map:
                        knowledge_results.append(m_map[rowid])
                        
        except Exception as e:
            log.error("Unified vector search failed: %s", e)

    # 2. 동적 메모리 (observations LIKE)
    try:
        obs_rows = conn.execute(
            OBSERVATIONS_LIKE_RECENT,
            (f"%{query}%", limit * multiplier)
        ).fetchall()
        for r in obs_rows:
            obs_results.append(dict(r))
    except Exception as e:
        log.error("Observation search failed: %s", e)
        
    # 3. RRF 병합
    rrf_map = {}
    item_details = {}
    
    def _add_to_rrf(domain, item_list, id_field):
        for i, item in enumerate(item_list):
            key = f"{domain}:{item[id_field]}"
            score = 1.0 / (i + rrf_k)
            rrf_map[key] = rrf_map.get(key, 0.0) + score
            item_details[key] = (domain, item)

    _add_to_rrf("code", code_results, "fqn")
    _add_to_rrf("knowledge", knowledge_results, "key")
    _add_to_rrf("observation", obs_results, "id")
    
    # 4. FTS Fallback (임베딩 실패 시 또는 결과 보완용)
    # _fts_search는 내부에서 독립 conn을 열므로 여기서는 별도 호출 유지
    fts_mems = _fts_search(workspace, query, limit=limit, multiplier=multiplier)
    _add_to_rrf("knowledge", fts_mems, "key")
    
    # 코드 FTS도 이미 열린 conn을 재사용하여 커넥션 낭비 방지
    try:
        from cortex.db import search_nodes_fts
        fts_nodes = search_nodes_fts(conn, query, limit=limit)
        _add_to_rrf("code", fts_nodes, "fqn")
    except Exception:
        pass
    finally:
        conn.close()

    # 5. 정렬 및 결과 포맷팅
    final_keys = sorted(
        rrf_map.keys(),
        key=lambda k: rrf_map[k] + (
            _heuristic_boost(item_details[k][1].get("key", ""), item_details[k][1].get("category", ""), query)
            if item_details[k][0] == "knowledge" else 0.0
        ),
        reverse=True
    )[:limit]
    
    final = []
    for k in final_keys:
        domain, item = item_details[k]
        base_score = rrf_map[k]
        boost = 0.0
        
        if domain == "code":
            res = {
                "domain": "code",
                "key": item.get("fqn", ""),
                "category": item.get("type", "unknown"),
                "file_path": item.get("file_path", ""),
                "snippet": _code_result_snippet(item),
            }
        elif domain == "knowledge":
            boost = _heuristic_boost(item.get("key", ""), item.get("category", ""), query)
            res = {
                "domain": "knowledge",
                "key": item.get("key", ""),
                "category": item.get("category", "unknown"),
                "snippet": item.get("content", "")[:snippet_len],
            }
        else: # observation
            res = {
                "domain": "observation",
                "key": str(item.get("id", "")),
                "category": item.get("type", "observation"),
                "snippet": item.get("content", "")[:snippet_len],
            }
            
        res["_total_score"] = round(base_score + boost, 6)
        final.append(res)
        
    return final
