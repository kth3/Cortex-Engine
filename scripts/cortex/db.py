"""
Cortex DB 모듈
SQLite 스키마 생성 및 쿼리 헬퍼
"""
import sqlite3
import os
from pathlib import Path

# DB 파일 경로: 프로젝트 내 .agents/data/memories.db
def get_db_path(workspace: str) -> str:
    if workspace.endswith(".agents"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".agents")
        
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "memories.db")

def to_rel_path(full_path: str, workspace: str) -> str:
    """절대 경로를 워크스페이스 기준 상대 경로(ROOT/...)로 변환"""
    if not full_path or not workspace:
        return full_path
    try:
        rel = os.path.relpath(full_path, workspace)
        return os.path.join("ROOT", rel).replace("\\", "/")
    except Exception:
        return full_path

def to_abs_path(rel_path: str, workspace: str) -> str:
    """ROOT/... 형식의 상대 경로를 현재 환경의 절대 경로로 복원"""
    if not rel_path or not workspace or not rel_path.startswith("ROOT"):
        return rel_path
    return os.path.abspath(os.path.join(workspace, rel_path.replace("ROOT/", "").replace("ROOT\\", "")))

# Fix #5: sqlite-vec 확장 로드 상태를 모듈 레벨에서 관리
# (sqlite3.Connection은 C 확장 타입이라 임의 속성 할당 불가)
_VEC_AVAILABLE = None  # None=미확인, True/False=확인 완료

def is_vec_available() -> bool:
    """sqlite-vec 확장 로드 가능 여부를 반환"""
    return _VEC_AVAILABLE is True

def get_connection(workspace: str) -> sqlite3.Connection:
    global _VEC_AVAILABLE
    db_path = get_db_path(workspace)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    
    # [NEW] V2 핵심: sqlite-vec 벡터 확장 모듈 직접 로드
    # Fix #5: 로드 성공 여부를 모듈 레벨 플래그로 기록하여 Graceful Degradation 지원
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_AVAILABLE = True
    except Exception as e:
        if _VEC_AVAILABLE is None:
            import sys
            sys.stderr.write(f"[Cortex DB] sqlite-vec unavailable, falling back to FTS5-only: {e}\n")
        _VEC_AVAILABLE = False
        
    return conn

def _create_core_tables(conn: sqlite3.Connection):
    """핵심 테이블 생성 (파일 캐시, 노드, 엣지 등)"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS file_cache (
        file_path   TEXT PRIMARY KEY,
        hash        TEXT NOT NULL,
        last_indexed_at INTEGER NOT NULL,
        node_count  INTEGER DEFAULT 0,
        workspace_id TEXT DEFAULT 'default'
    );

    CREATE TABLE IF NOT EXISTS nodes (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        name        TEXT NOT NULL,
        fqn         TEXT NOT NULL,
        file_path   TEXT NOT NULL,
        start_line  INTEGER NOT NULL,
        end_line    INTEGER NOT NULL,
        signature   TEXT,
        return_type TEXT,
        docstring   TEXT,
        is_exported INTEGER DEFAULT 1,
        is_async    INTEGER DEFAULT 0,
        is_test     INTEGER DEFAULT 0,
        raw_body    TEXT,
        skeleton_standard TEXT,
        skeleton_minimal  TEXT,
        language    TEXT NOT NULL,
        module      TEXT DEFAULT 'unknown',
        workspace_id TEXT DEFAULT 'default',
        category    TEXT DEFAULT 'SOURCE'
    );

    CREATE TABLE IF NOT EXISTS edges (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id   TEXT NOT NULL,
        target_id   TEXT NOT NULL,
        type        TEXT NOT NULL DEFAULT 'CALLS',
        call_site_line INTEGER,
        confidence  REAL DEFAULT 1.0,
        UNIQUE(source_id, target_id, type)
    );
    """)

def _create_history_tables(conn: sqlite3.Connection):
    """Git 이력 및 변경 이력 추적용 테이블 생성"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS file_lineage (
        file_path       TEXT PRIMARY KEY,
        commit_count    INTEGER DEFAULT 0,
        churn_score     REAL DEFAULT 0.0,
        last_author     TEXT DEFAULT '',
        last_commit_ts  INTEGER DEFAULT 0,
        updated_at      INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS co_change_edges (
        file_a          TEXT NOT NULL,
        file_b          TEXT NOT NULL,
        coupling_score  REAL NOT NULL,
        shared_commits  INTEGER NOT NULL,
        updated_at      INTEGER NOT NULL,
        PRIMARY KEY (file_a, file_b)
    );

    CREATE TABLE IF NOT EXISTS ast_diffs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path       TEXT NOT NULL,
        symbol_fqn      TEXT NOT NULL,
        diff_type       TEXT NOT NULL,
        summary         TEXT NOT NULL,
        old_snippet     TEXT,
        new_snippet     TEXT,
        detected_at     INTEGER NOT NULL
    );
    """)

def _create_memory_tables(conn: sqlite3.Connection):
    """에이전트 메모리, 관찰, 세션용 테이블 생성"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        agent_name      TEXT DEFAULT 'unknown',
        started_at      INTEGER,
        last_active_at  INTEGER,
        status          TEXT DEFAULT 'active',
        summary         TEXT,
        tool_call_count INTEGER DEFAULT 0,
        observation_count INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS observations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT,
        type            TEXT NOT NULL,
        content         TEXT NOT NULL,
        file_paths      TEXT,
        stale           INTEGER DEFAULT 0,
        created_at      INTEGER NOT NULL,
        source          TEXT DEFAULT 'agent',
        confidence      REAL DEFAULT 1.0,
        category        TEXT
    );

    CREATE TABLE IF NOT EXISTS memories (
        key         TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        category    TEXT NOT NULL,
        content     TEXT NOT NULL,
        tags        TEXT,
        relationships TEXT,
        access_count INTEGER DEFAULT 0,
        created_at  INTEGER NOT NULL,
        updated_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS search_misses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        query       TEXT NOT NULL,
        project_id  TEXT,
        category    TEXT,
        created_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS meta (
        key     TEXT PRIMARY KEY,
        value   TEXT
    );
    """)


def _create_vec_tables(conn: sqlite3.Connection):
    """Fix #5: sqlite-vec 가상 테이블 생성 (확장이 로드된 경우에만 호출)"""
    conn.executescript("""
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
        rowid INTEGER PRIMARY KEY,
        embedding float[1024]
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS vec_nodes USING vec0(
        rowid INTEGER PRIMARY KEY,
        embedding float[1024]
    );

    -- sqlite-vec 고아 벡터 방지용 CASCADE 트리거
    CREATE TRIGGER IF NOT EXISTS del_node_vec AFTER DELETE ON nodes BEGIN
        DELETE FROM vec_nodes WHERE rowid = old.rowid;
    END;

    CREATE TRIGGER IF NOT EXISTS del_memory_vec AFTER DELETE ON memories BEGIN
        DELETE FROM vec_memories WHERE rowid = old.rowid;
    END;
    """)

def _create_fts_and_triggers(conn: sqlite3.Connection):
    """FTS5 인덱스와 연관 트리거 생성"""
    conn.executescript("""
    CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
        name, fqn, docstring, signature,
        content='nodes',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
        INSERT INTO nodes_fts(rowid, name, fqn, docstring, signature)
        VALUES (new.rowid, new.name, new.fqn, new.docstring, new.signature);
    END;

    CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, name, fqn, docstring, signature)
        VALUES ('delete', old.rowid, old.name, old.fqn, old.docstring, old.signature);
    END;

    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        key, content, tags, category,
        content='memories',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, key, content, tags, category)
        VALUES (new.rowid, new.key, new.content, new.tags, new.category);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, key, content, tags, category)
        VALUES ('delete', old.rowid, old.key, old.content, old.tags, old.category);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, key, content, tags, category)
        VALUES ('delete', old.rowid, old.key, old.content, old.tags, old.category);
        INSERT INTO memories_fts(rowid, key, content, tags, category)
        VALUES (new.rowid, new.key, new.content, new.tags, new.category);
    END;
    """)

def _create_indexes(conn: sqlite3.Connection):
    """성능 최적화용 일반 인덱스 생성"""
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
    CREATE INDEX IF NOT EXISTS idx_nodes_fqn ON nodes(fqn);
    CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
    CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
    CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
    CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
    CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);
    CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
    CREATE INDEX IF NOT EXISTS idx_search_misses_ts ON search_misses(created_at);
    """)

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

def init_schema(conn: sqlite3.Connection):
    """DB 스키마 생성 및 마이그레이션 (동적 인덱싱용)"""
    _create_core_tables(conn)
    _create_history_tables(conn)
    _create_memory_tables(conn)
    _create_fts_and_triggers(conn)
    _create_indexes(conn)

    # Fix #5: sqlite-vec 가상 테이블은 확장이 로드된 경우에만 생성
    if is_vec_available():
        _create_vec_tables(conn)

    # 초기화 및 마이그레이션
    _apply_migrations(conn)
    
    # 메타 정보 초기화
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", "1")
    )
    conn.commit()

# ==============================================================================
# 쿼리 헬퍼
# ==============================================================================

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
           WHERE e.target_id = ?""",
        (node_id,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_callees(conn: sqlite3.Connection, node_id: str):
    """특정 노드가 호출하는 타겟 노드 목록"""
    rows = conn.execute(
        """SELECT n.*, e.type as edge_type, e.call_site_line
           FROM edges e JOIN nodes n ON n.id = e.target_id
           WHERE e.source_id = ?""",
        (node_id,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_stats(conn: sqlite3.Connection) -> dict:
    """인덱스 통계"""
    node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    file_count = conn.execute("SELECT COUNT(*) FROM file_cache").fetchone()[0]
    return {
        "total_nodes": node_count,
        "total_edges": edge_count,
        "total_files": file_count,
        "schema_version": conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
    }
