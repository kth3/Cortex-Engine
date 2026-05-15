"""SQLite connection and extension management.

- 책임: SQLite DB 파일 연결을 생성하고 PRAGMA 설정을 초기화하며, sqlite-vec(벡터 검색 확장) 모듈을 로드하는 책임을 가진다.
"""
import sqlite3
from cortex.paths import data_dir
from cortex.logger import get_logger

LOG_NAME = "storage"

DB_FILENAME = "memories.db"
SQLITE_CONNECT_TIMEOUT_SECONDS = 10

PRAGMA_JOURNAL_MODE_WAL = "PRAGMA journal_mode=WAL"
PRAGMA_BUSY_TIMEOUT = "PRAGMA busy_timeout=5000"
PRAGMA_FOREIGN_KEYS_ON = "PRAGMA foreign_keys=ON"

SQLITE_VEC_UNAVAILABLE_WARNING = "sqlite-vec unavailable, falling back to FTS5-only: %s"

log = get_logger(LOG_NAME)


def get_db_path(workspace: str) -> str:
    """DB 파일 경로: 프로젝트 내 .cortex/data/memories.db"""
    return str(data_dir(workspace) / DB_FILENAME)


# sqlite-vec 확장 로드 상태를 모듈 레벨에서 관리
_VEC_AVAILABLE = None  # None=미확인, True/False=확인 완료


def is_vec_available() -> bool:
    """sqlite-vec 확장 로드 가능 여부를 반환"""
    return _VEC_AVAILABLE is True


def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path, timeout=SQLITE_CONNECT_TIMEOUT_SECONDS)


def _configure_row_factory(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute(PRAGMA_JOURNAL_MODE_WAL)
    conn.execute(PRAGMA_BUSY_TIMEOUT)
    conn.execute(PRAGMA_FOREIGN_KEYS_ON)


def _load_sqlite_vec_extension(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:
        if _VEC_AVAILABLE is None:
            log.warning(SQLITE_VEC_UNAVAILABLE_WARNING, e)
        return False


def get_connection(workspace: str) -> sqlite3.Connection:
    global _VEC_AVAILABLE

    db_path = get_db_path(workspace)
    conn = _connect_sqlite(db_path)
    _configure_row_factory(conn)
    _apply_pragmas(conn)

    _VEC_AVAILABLE = _load_sqlite_vec_extension(conn)

    return conn
