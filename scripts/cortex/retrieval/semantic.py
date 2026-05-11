from cortex.db import get_connection
from cortex.logger import get_logger

log = get_logger("search_engine")

def _vector_search(workspace: str, query: str, category: str = None,
                   limit: int = 10, multiplier: int = 2, ve_module=None) -> list:
    """sqlite-vec 기반 벡터 유사도 검색"""
    if ve_module is None:
        return []

    results = []
    conn = get_connection(workspace)
    try:
        from cortex.embeddings.hardware import detect_gpu
        # ve_module 대신 get_embeddings를 직접 호출할 수도 있으나, 
        # 기존 search_engine.py에서 파라미터로 받은 ve_module을 활용하는 구조를 유지.
        # 단, embeddings.get_embeddings를 직접 쓰는 것도 좋지만 여기선 기존 호환을 위해 유지하거나
        # 아니면 명시적으로 cortex.embeddings를 import해서 쓰자.
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
