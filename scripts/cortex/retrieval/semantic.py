from cortex.db import get_connection
from cortex.logger import get_logger
from cortex.retrieval import DEFAULT_LIMIT, DEFAULT_MULTIPLIER

log = get_logger("search_engine")

def _vector_search(workspace: str, query: str, category: str = None,
                   limit: int = DEFAULT_LIMIT, multiplier: int = DEFAULT_MULTIPLIER, ve_module=None) -> list:
    """sqlite-vec 기반 벡터 유사도 검색"""
    if ve_module is None:
        return []

    results = []
    conn = get_connection(workspace)
    try:
        from cortex.embeddings.hardware import detect_gpu
        # 호환성을 위해 ve_module을 활용한 임베딩 호출 유지
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
