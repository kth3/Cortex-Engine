"""SQL query constants for retrieval modules.

이 파일은 retrieval 계층의 SQL 문자열만 모아둔다.
쿼리 의미를 변경하지 않고, 기존 함수 내부 SQL을 이동하는 용도다.
"""

FTS_MEMORIES_WITH_CATEGORY = """
SELECT m.* FROM memories_fts f
JOIN memories m ON m.rowid = f.rowid
WHERE memories_fts MATCH ? AND m.category = ?
ORDER BY rank LIMIT ?
"""

FTS_MEMORIES = """
SELECT m.* FROM memories_fts f
JOIN memories m ON m.rowid = f.rowid
WHERE memories_fts MATCH ?
ORDER BY rank LIMIT ?
"""

VECTOR_MEMORY_ROWIDS = "SELECT rowid FROM vec_memories WHERE embedding MATCH ? AND k = ?"

VECTOR_NODE_ROWIDS = "SELECT rowid FROM vec_nodes WHERE embedding MATCH ? AND k = ?"

OBSERVATIONS_LIKE_RECENT = """
SELECT * FROM observations
WHERE content LIKE ?
ORDER BY created_at DESC LIMIT ?
"""


def placeholders(count: int) -> str:
    """Return a comma-separated placeholder list for SQLite IN clauses."""
    return ",".join(["?"] * count)


def select_memories_by_rowids(count: int, include_rowid: bool = False) -> str:
    """Build a memories rowid lookup query.

    include_rowid=False:
        SELECT * FROM memories WHERE rowid IN (?, ?, ...)

    include_rowid=True:
        SELECT rowid, * FROM memories WHERE rowid IN (?, ?, ...)
    """
    selected_columns = "rowid, *" if include_rowid else "*"
    return f"SELECT {selected_columns} FROM memories WHERE rowid IN ({placeholders(count)})"


def select_nodes_by_rowids(count: int) -> str:
    """Build a nodes rowid lookup query."""
    return f"SELECT rowid, * FROM nodes WHERE rowid IN ({placeholders(count)})"
