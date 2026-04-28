"""
Cortex 하이브리드 검색 엔진 (v1.1)
FTS5 + sqlite-vec 벡터 검색 + RRF(Reciprocal Rank Fusion) 스코어링.
persistent_memory.py의 search_knowledge 로직에서 분리됨.
튜닝 파라미터는 indexer_utils.get_tuning_params()에서 동적 주입.
"""
import json
from cortex.db import get_connection
from cortex.logger import get_logger
from cortex.indexer_utils import get_tuning_params

log = get_logger("search_engine")


def _heuristic_boost(item_key: str, item_category: str, query: str) -> float:
    """휴리스틱 가중치 계산
    
    [수정] 외부 레퍼런스(resource, example)에 페널티를 부여하여
    프로젝트 규칙/프로토콜/스킬이 검색 결과 상단에 오도록 보장.
    """
    boost = 0.0
    q_low = query.lower()
    k_low = item_key.lower()
    if k_low == q_low:
        boost += 0.5
    elif q_low in k_low:
        boost += 0.1
    if item_category in ["rule", "skill", "decision", "protocol"]:
        boost += 0.05
    # 외부 레퍼런스 페널티: RRF Hub 편향 현상 방지
    if item_category in ["resource", "example"]:
        boost -= 0.3
    return boost


def _fts_search(workspace: str, query: str, category: str = None,
                limit: int = 10, multiplier: int = 2) -> list:
    """FTS5 기반 키워드 검색"""
    results = []
    conn = get_connection(workspace)
    try:
        clean_query = query.replace('"', '').replace("'", "")
        tokens = [f'"{t}"*' for t in clean_query.split() if len(t) >= 2]
        fts_query = " OR ".join(tokens) if tokens else "*"

        fetch_limit = limit * multiplier
        if category:
            rows = conn.execute(
                """SELECT m.* FROM memories_fts f
                   JOIN memories m ON m.rowid = f.rowid
                   WHERE memories_fts MATCH ? AND m.category = ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, category, fetch_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.* FROM memories_fts f
                   JOIN memories m ON m.rowid = f.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, fetch_limit),
            ).fetchall()

        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["relationships"] = json.loads(d.get("relationships") or "{}")
            results.append(d)
    except Exception:
        pass
    finally:
        conn.close()
    return results


def _vector_search(workspace: str, query: str, category: str = None,
                   limit: int = 10, multiplier: int = 2, ve_module=None) -> list:
    """sqlite-vec 기반 벡터 유사도 검색"""
    if ve_module is None:
        return []

    results = []
    conn = get_connection(workspace)
    try:
        from cortex.vectorizer import detect_gpu
        query_vec = ve_module.get_embeddings([query], use_gpu=detect_gpu())[0]
        vec_rows = conn.execute(
            "SELECT rowid FROM vec_memories WHERE embedding MATCH ? AND k = ?",
            (query_vec.tobytes(), limit * multiplier)
        ).fetchall()
        if vec_rows:
            rowids = [r[0] for r in vec_rows]
            ph = ",".join(["?"] * len(rowids))
            db_rows = conn.execute(
                f"SELECT * FROM memories WHERE rowid IN ({ph})", rowids
            ).fetchall()
            for r in db_rows:
                d = dict(r)
                if not category or d.get("category") == category:
                    results.append({
                        "id": d["key"],
                        "text": d.get("content", ""),
                        "meta": {"category": d.get("category", "unknown")}
                    })
    except Exception as e:
        log.error("Vector search failed: %s", e)
    finally:
        conn.close()
    return results


def hybrid_search(workspace: str, query: str, category: str = None, limit: int = 10, ve_module=None) -> list:
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


def unified_pipeline_search(workspace: str, query: str, limit: int = 10, ve_module=None) -> list:
    """
    코드(vec_nodes) + 지식(vec_memories) + 동적메모리(observations FTS/LIKE)를
    단일 임베딩으로 교차 RRF 검색.
    """
    params = get_tuning_params(workspace)
    snippet_len = params["search_snippet_len"]
    multiplier = params["search_multiplier"]
    rrf_k = params["rrf_k"]

    conn = get_connection(workspace)
    
    code_results = []
    knowledge_results = []
    obs_results = []
    
    # 1. 단일 임베딩 및 벡터 검색
    if ve_module is not None:
        try:
            from cortex.vectorizer import detect_gpu
            query_vec = ve_module.get_embeddings([query], use_gpu=detect_gpu())[0]
            
            # vec_nodes (코드 도메인)
            vec_nodes_rows = conn.execute(
                "SELECT rowid FROM vec_nodes WHERE embedding MATCH ? AND k = ?",
                (query_vec.tobytes(), limit * multiplier)
            ).fetchall()
            
            if vec_nodes_rows:
                rowids = [r[0] for r in vec_nodes_rows]
                ph = ",".join(["?"] * len(rowids))
                # rowid는 SELECT *에 포함되지 않으므로 명시적으로 선택
                db_nodes = conn.execute(
                    f"SELECT rowid, * FROM nodes WHERE rowid IN ({ph})", rowids
                ).fetchall()
                n_map = {r["rowid"]: dict(r) for r in db_nodes}
                for rowid in rowids:
                    if rowid in n_map:
                        code_results.append(n_map[rowid])
            
            # vec_memories (지식 도메인)
            vec_mem_rows = conn.execute(
                "SELECT rowid FROM vec_memories WHERE embedding MATCH ? AND k = ?",
                (query_vec.tobytes(), limit * multiplier)
            ).fetchall()
            
            if vec_mem_rows:
                rowids = [r[0] for r in vec_mem_rows]
                ph = ",".join(["?"] * len(rowids))
                db_mems = conn.execute(
                    f"SELECT rowid, * FROM memories WHERE rowid IN ({ph})", rowids
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
            "SELECT * FROM observations WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
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
                "snippet": "→ Capsule 참조 (코드 생략됨)",
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
