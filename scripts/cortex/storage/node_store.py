import sqlite3

def search_nodes_fts(conn: sqlite3.Connection, query: str, category: str = None, limit: int = 10):
    """FTS5 전문 검색 - 각 단어를 독립 토큰으로 검색, 카테고리 필터링 지원"""
    import re
    # 영문/한글 단어 토큰만 추출 (2자 이상)
    tokens = [t for t in re.split(r'[\s\-_.,/]+', query) if len(t) >= 2]
    if not tokens:
        return []
    # 각 토큰을 FTS5 prefix 토큰으로 변환: token* OR token*
    safe_tokens = " OR ".join(f'"{t}"*' for t in tokens)
    try:
        if category:
            rows = conn.execute(
                """SELECT n.* FROM nodes_fts f
                   JOIN nodes n ON n.rowid = f.rowid
                   WHERE nodes_fts MATCH ? AND n.category = ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_tokens, category, limit)
            ).fetchall()
        else:
            # 카테고리가 지정되지 않으면 SOURCE(코드)에 우선순위 부여
            rows = conn.execute(
                """SELECT n.* FROM nodes_fts f
                   JOIN nodes n ON n.rowid = f.rowid
                   WHERE nodes_fts MATCH ?
                   ORDER BY CASE WHEN n.category = 'SOURCE' THEN 0 ELSE 1 END, rank
                   LIMIT ?""",
                (safe_tokens, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def get_node_by_fqn(conn: sqlite3.Connection, fqn: str):
    """FQN으로 노드 조회"""
    row = conn.execute("SELECT * FROM nodes WHERE fqn = ?", (fqn,)).fetchone()
    return dict(row) if row else None

def get_node_by_id(conn: sqlite3.Connection, node_id: str):
    """ID로 노드 조회"""
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return dict(row) if row else None

def get_callers(conn: sqlite3.Connection, node_id: str):
    """특정 노드를 호출하는 소스 노드 목록"""
    rows = conn.execute(
        """SELECT n.*, e.type as edge_type, e.call_site_line
           FROM edges e JOIN nodes n ON n.id = e.source_id
           WHERE e.target_id = ?
              OR e.target_id = '__unresolved__::' || (SELECT name FROM nodes WHERE id = ?)""",
        (node_id, node_id)
    ).fetchall()
    return [dict(r) for r in rows]

def get_callees(conn: sqlite3.Connection, node_id: str):
    """특정 노드가 호출하는 타겟 노드 목록"""
    rows = conn.execute(
        """SELECT DISTINCT n.*, e.type as edge_type, e.call_site_line
           FROM edges e JOIN nodes n
             ON (n.id = e.target_id
                 OR e.target_id = '__unresolved__::' || n.name)
           WHERE e.source_id = ?""",
        (node_id,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_stats(conn: sqlite3.Connection) -> dict:
    """인덱스 통계"""
    node_count    = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edge_count    = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    file_count    = conn.execute("SELECT COUNT(*) FROM file_cache").fetchone()[0]
    memory_count  = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    return {
        "total_nodes":    node_count,
        "total_edges":    edge_count,
        "total_files":    file_count,
        "total_memories": memory_count,
        "schema_version": conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
    }
