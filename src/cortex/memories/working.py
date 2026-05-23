"""
에이전트의 관찰(Observations), 결정, 통찰을 저장하고 검색하는 세션 메모리 모듈.
"""
import time
from cortex.storage import get_connection

def save_observation(workspace, session_id, obs_type, content, file_paths=None):
    """
    에이전트의 관찰 기록 저장
    - obs_type: insight, decision, error, pattern
    """
    conn = get_connection(workspace)
    try:
        conn.execute(
            "INSERT INTO observations (session_id, type, content, file_paths, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, obs_type, content, ",".join(file_paths) if file_paths else None, int(time.time()))
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving observation: {e}")
        return False
    finally:
        conn.close()

def search_memory(workspace, query, limit=10):
    """
    과거 관찰 기록 검색
    (현재는 단순 LIKE 검색이나, 추후 observations용 FTS5 테이블 추가 가능)
    """
    conn = get_connection(workspace)
    try:
        rows = conn.execute(
            "SELECT * FROM observations WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit)
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_session_context(workspace, session_id):
    """
    특정 세션의 모든 활동 기록 조회
    """
    conn = get_connection(workspace)
    try:
        rows = conn.execute(
            "SELECT * FROM observations WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,)
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()
