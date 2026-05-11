import sqlite3

def _apply_migrations(conn: sqlite3.Connection):
    """기존 스키마에 대한 마이그레이션 적용"""
    # nodes 테이블 마이그레이션 - PRAGMA 결과는 Row가 아닌 튜플일 수 있으므로 인덱스로 접근
    node_cols = conn.execute("PRAGMA table_info(nodes)").fetchall()
    columns = [c[1] for c in node_cols] # 1번 인덱스가 'name'
    
    if 'module' not in columns:
        conn.execute("ALTER TABLE nodes ADD COLUMN module TEXT DEFAULT 'unknown'")
    if 'workspace_id' not in columns:
        conn.execute("ALTER TABLE nodes ADD COLUMN workspace_id TEXT DEFAULT 'default'")
    if 'category' not in columns:
        conn.execute("ALTER TABLE nodes ADD COLUMN category TEXT DEFAULT 'SOURCE'")

    # file_cache 테이블 마이그레이션
    cache_cols_info = conn.execute("PRAGMA table_info(file_cache)").fetchall()
    cache_columns = [c[1] for c in cache_cols_info]
    if 'workspace_id' not in cache_columns:
        conn.execute("ALTER TABLE file_cache ADD COLUMN workspace_id TEXT DEFAULT 'default'")

    conn.commit()
