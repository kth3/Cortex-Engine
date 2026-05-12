import sqlite3
from cortex.paths import data_dir
from cortex.logger import get_logger

log = get_logger("storage")

def get_db_path(workspace: str) -> str:
    """DB 파일 경로: 프로젝트 내 .agents/data/memories.db"""
    return str(data_dir(workspace) / "memories.db")

# sqlite-vec 확장 로드 상태를 모듈 레벨에서 관리
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
    
    # sqlite-vec 벡터 확장 모듈 직접 로드
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_AVAILABLE = True
    except Exception as e:
        if _VEC_AVAILABLE is None:
            log.warning("sqlite-vec unavailable, falling back to FTS5-only: %s", e)
        _VEC_AVAILABLE = False
        
    return conn
