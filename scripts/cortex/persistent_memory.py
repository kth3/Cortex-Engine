"""
영구 지식 저장소 관리자 (PersistentMemoryManager)
- db.py의 memories 테이블(FTS5 포함)을 사용
- agent-memory-mcp 흡수 통합 버전
"""
import time
import json
from cortex.db import get_connection, init_schema


class PersistentMemoryManager:
    def __init__(self, workspace: str):
        self.workspace = workspace
        # 스키마 보장
        conn = get_connection(workspace)
        try:
            init_schema(conn)
        finally:
            conn.close()

    def write(self, project_id: str, data: dict) -> bool:
        """
        영구 지식 저장 또는 갱신
        data: {key, category, content, tags=[], relationships={}}
        """
        key = data.get("key", "")
        if not key:
            return False

        conn = get_connection(self.workspace)
        try:
            now = int(time.time())
            tags_json = json.dumps(data.get("tags") or [], ensure_ascii=False)
            rel_json = json.dumps(data.get("relationships") or {}, ensure_ascii=False)

            existing = conn.execute(
                "SELECT key FROM memories WHERE key = ?", (key,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE memories
                       SET category=?, content=?, tags=?, relationships=?, updated_at=?,
                           access_count=access_count+1
                       WHERE key=?""",
                    (
                        data.get("category", "general"),
                        data.get("content", ""),
                        tags_json,
                        rel_json,
                        now,
                        key,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO memories
                       (key, project_id, category, content, tags, relationships, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key,
                        project_id,
                        data.get("category", "general"),
                        data.get("content", ""),
                        tags_json,
                        rel_json,
                        now,
                        now,
                    ),
                )
            conn.commit()
            return True
        except Exception as e:
            print(f"[persistent_memory] write error: {e}")
            return False
        finally:
            conn.close()

    def read(self, project_id: str, key: str) -> dict:
        """키로 단일 메모리 조회"""
        conn = get_connection(self.workspace)
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE key = ?", (key,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE memories SET access_count=access_count+1 WHERE key=?", (key,)
                )
                conn.commit()
                d = dict(row)
                d["tags"] = json.loads(d.get("tags") or "[]")
                d["relationships"] = json.loads(d.get("relationships") or "{}")
                return d
            return {"error": f"Key '{key}' not found"}
        finally:
            conn.close()

    def search(self, project_id: str, query: str, category: str = None, limit: int = 10) -> list:
        """하이브리드 검색 (FTS5 + 벡터 유사도 병합)"""
        from cortex import vector_engine as ve
        
        results_map = {} # key -> data
        
        # 1. 벡터 검색 (의미 기반)
        try:
            vector_results = ve.search_similar(self.workspace, query, top_k=limit)
            for vr in vector_results:
                # search_similar는 DB에서 실제 데이터를 가져오지 않으므로 read로 보충
                mem_data = self.read(project_id, vr["id"])
                if "error" not in mem_data:
                    if not category or mem_data.get("category") == category:
                        results_map[vr["id"]] = mem_data
        except Exception as e:
            import sys
            sys.stderr.write(f"[persistent_memory] Vector search failed: {e}\n")

        # 2. FTS5 전문 검색 (키워드 기반)
        conn = get_connection(self.workspace)
        try:
            clean_query = query.replace('"', '').replace("'", "")
            tokens = [f'"{t}"*' for t in clean_query.split() if len(t) >= 2]
            fts_query = " OR ".join(tokens) if tokens else "*"
            
            if category:
                rows = conn.execute(
                    """SELECT m.* FROM memories_fts f
                       JOIN memories m ON m.rowid = f.rowid
                       WHERE memories_fts MATCH ? AND m.category = ?
                       ORDER BY rank LIMIT ?""",
                    (fts_query, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT m.* FROM memories_fts f
                       JOIN memories m ON m.rowid = f.rowid
                       WHERE memories_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (fts_query, limit),
                ).fetchall()

            for row in rows:
                d = dict(row)
                if d["key"] not in results_map:
                    d["tags"] = json.loads(d.get("tags") or "[]")
                    d["relationships"] = json.loads(d.get("relationships") or "{}")
                    results_map[d["key"]] = d
        except Exception:
            pass # FTS 실패 시 벡터 결과에 의존
        finally:
            conn.close()

        return list(results_map.values())[:limit]

    def delete_many(self, project_id: str, keys: list) -> int:
        """주어진 key 리스트에 해당하는 메모리 레코드를 영구 삭제 (FTS 동기화 포함)"""
        if not keys:
            return 0
        conn = get_connection(self.workspace)
        try:
            # ⚡ Bolt Optimization: Replace single large IN clause with chunked IN clauses
            # Why: Bypasses SQLite's 999 parameter limit preventing crashes on large deletions,
            # while maintaining the high performance of IN clauses over single-row executemany.
            deleted_count = 0
            chunk_size = 900
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                cursor = conn.execute(f"DELETE FROM memories WHERE key IN ({placeholders})", chunk)
                deleted_count += cursor.rowcount
            conn.commit()
            return deleted_count
        except Exception as e:
            print(f"[persistent_memory] delete error: {e}")
            return 0
        finally:
            conn.close()

    def get_stats(self, project_id: str) -> dict:
        """메모리 저장소 통계"""
        conn = get_connection(self.workspace)
        try:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_cat = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories GROUP BY category"
            ).fetchall()
            return {
                "total_memories": total,
                "by_category": {r["category"]: r["cnt"] for r in by_cat},
            }
        finally:
            conn.close()
