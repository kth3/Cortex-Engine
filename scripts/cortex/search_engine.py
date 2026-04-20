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
    """휴리스틱 가중치 계산"""
    boost = 0.0
    q_low = query.lower()
    k_low = item_key.lower()
    if k_low == q_low:
        boost += 0.5
    elif q_low in k_low:
        boost += 0.1
    if item_category in ["rule", "skill", "decision", "protocol"]:
        boost += 0.05
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
