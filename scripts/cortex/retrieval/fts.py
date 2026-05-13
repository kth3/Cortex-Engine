"""FTS search engine.

- 책임: FTS5 기반의 키워드 및 형태소 일치 검색을 담당한다.
- Vector 검색이 잡지 못하는 정확한 식별자, 특수 용어, 고유 명사 검색에 강점을 가진다.
"""
import json
from cortex.db import get_connection
from cortex.logger import get_logger
from cortex.retrieval.constants import DEFAULT_LIMIT, DEFAULT_MULTIPLIER
from cortex.retrieval.queries import FTS_MEMORIES, FTS_MEMORIES_WITH_CATEGORY

log = get_logger("fts")

def _fts_search(workspace: str, query: str, category: str = None,
                limit: int = DEFAULT_LIMIT, multiplier: int = DEFAULT_MULTIPLIER) -> list:
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
                FTS_MEMORIES_WITH_CATEGORY,
                (fts_query, category, fetch_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                FTS_MEMORIES,
                (fts_query, fetch_limit),
            ).fetchall()

        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["relationships"] = json.loads(d.get("relationships") or "{}")
            results.append(d)
    except Exception as e:
        log.warning("FTS search failed: %s", e)
    finally:
        conn.close()
    return results
