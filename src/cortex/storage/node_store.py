import sqlite3
from cortex.storage.queries import (
    SEARCH_NODES_FTS_WITH_CATEGORY_SQL,
    SEARCH_NODES_FTS_SQL,
    SELECT_NODE_BY_FQN_SQL,
    SELECT_NODE_BY_ID_SQL,
    SELECT_CALLERS_SQL,
    SELECT_CALLEES_SQL,
    COUNT_NODES_SQL,
    COUNT_EDGES_SQL,
    COUNT_FILES_SQL,
    COUNT_MEMORIES_SQL,
    SELECT_SCHEMA_VERSION_SQL,
)

def search_nodes_fts(conn: sqlite3.Connection, query: str, category: str = None, limit: int = 10):
    """FTS5 전문 검색 - 구문 검색(phrase search)을 유지하도록 공백으로만 토큰화, 카테고리 필터링 지원"""
    from cortex.retrieval.fts_query import normalize_fts_query
    safe_tokens = normalize_fts_query(query)
    if not safe_tokens:
        return []
    
    try:
        if category:
            rows = conn.execute(
                SEARCH_NODES_FTS_WITH_CATEGORY_SQL,
                (safe_tokens, category, limit)
            ).fetchall()
        else:
            # 카테고리가 지정되지 않으면 SOURCE(코드)에 우선순위 부여
            rows = conn.execute(
                SEARCH_NODES_FTS_SQL,
                (safe_tokens, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def get_node_by_fqn(conn: sqlite3.Connection, fqn: str):
    """FQN으로 노드 조회"""
    row = conn.execute(SELECT_NODE_BY_FQN_SQL, (fqn,)).fetchone()
    return dict(row) if row else None

def get_node_by_id(conn: sqlite3.Connection, node_id: str):
    """ID로 노드 조회"""
    row = conn.execute(SELECT_NODE_BY_ID_SQL, (node_id,)).fetchone()
    return dict(row) if row else None

def get_callers(conn: sqlite3.Connection, node_id: str):
    """특정 노드를 호출하는 소스 노드 목록"""
    rows = conn.execute(SELECT_CALLERS_SQL, (node_id, node_id)).fetchall()
    return [dict(r) for r in rows]

def get_callees(conn: sqlite3.Connection, node_id: str):
    """특정 노드가 호출하는 타겟 노드 목록"""
    rows = conn.execute(SELECT_CALLEES_SQL, (node_id,)).fetchall()
    return [dict(r) for r in rows]

def get_stats(conn: sqlite3.Connection) -> dict:
    """인덱스 통계"""
    node_count    = conn.execute(COUNT_NODES_SQL).fetchone()[0]
    edge_count    = conn.execute(COUNT_EDGES_SQL).fetchone()[0]
    file_count    = conn.execute(COUNT_FILES_SQL).fetchone()[0]
    memory_count  = conn.execute(COUNT_MEMORIES_SQL).fetchone()[0]
    return {
        "total_nodes":    node_count,
        "total_edges":    edge_count,
        "total_files":    file_count,
        "total_memories": memory_count,
        "schema_version": conn.execute(SELECT_SCHEMA_VERSION_SQL).fetchone()[0]
    }
