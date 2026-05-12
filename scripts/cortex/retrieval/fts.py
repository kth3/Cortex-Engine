import json
from cortex.db import get_connection
from cortex.retrieval.constants import DEFAULT_LIMIT, DEFAULT_MULTIPLIER
from cortex.logger import get_logger

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
    except Exception as e:
        log.warning("FTS search failed: %s", e)
    finally:
        conn.close()
    return results
