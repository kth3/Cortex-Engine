"""
영구 지식 저장소 관리자 (PersistentMemoryManager)
- db.py의 memories 테이블(FTS5 포함)을 사용
- agent-memory-mcp 흡수 통합 버전
- 하이브리드 검색 로직은 search_engine.py로 위임
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
        res = self.read_batch(project_id, [key])
        return res.get(key, {"error": f"Key '{key}' not found"})

    def read_batch(self, project_id: str, keys: list) -> dict:
        """다수의 키를 이용해 메모리 일괄 조회 (N+1 최적화)"""
        if not keys:
            return {}

        conn = get_connection(self.workspace)
        fetched_data = {}
        try:
            chunk_size = 900
            # 1단계: access_count 일괄 업데이트 (UPDATE 먼저)
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                conn.execute(f"UPDATE memories SET access_count=access_count+1 WHERE key IN ({placeholders})", chunk)
            conn.commit()

            # 2단계: Batch read (SELECT)
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                query_sql = f"SELECT * FROM memories WHERE key IN ({placeholders})"
                db_rows = conn.execute(query_sql, chunk).fetchall()
                for db_row in db_rows:
                    # Row 객체를 안전하게 dict로 변환 (튜플 가능성 대비)
                    d = dict(db_row)
                    d["tags"] = json.loads(d.get("tags") or "[]")
                    d["relationships"] = json.loads(d.get("relationships") or "{}")
                    fetched_data[d["key"]] = d

            return fetched_data
        finally:
            conn.close()

    def search(self, project_id: str, query: str, category: str = None, limit: int = 10) -> list:
        """하이브리드 검색 (FTS5 + 벡터 유사도 병합)"""
        from cortex import vector_engine as ve

        results_map = {}  # key -> data

        # Fix #4: 단일 커넥션으로 두 검색 모두 처리하여 커넥션 누수 방지
        conn = get_connection(self.workspace)
        try:
            # 1. 벡터 검색 (sqlite-vec 기반) — Fix #5: 확장 로드 여부를 체크
            from cortex.db import is_vec_available
            if is_vec_available():
                try:
                    query_vec = ve.get_embeddings([query])[0]
                    vec_rows = conn.execute("SELECT rowid FROM vec_memories WHERE embedding MATCH ? AND k = ?", (query_vec.tobytes(), limit * 2)).fetchall()
                    if vec_rows:
                        ph = ",".join(["?"] * len(vec_rows))
                        rowids = [r[0] for r in vec_rows]
                        db_rows = conn.execute(f"SELECT * FROM memories WHERE rowid IN ({ph})", rowids).fetchall()
                        for r in db_rows:
                            d = dict(r)
                            if not category or d.get("category") == category:
                                d["tags"] = json.loads(d.get("tags") or "[]")
                                d["relationships"] = json.loads(d.get("relationships") or "{}")
                                results_map[d["key"]] = d
                except Exception as e:
                    import sys as _sys
                    _sys.stderr.write(f"[persistent_memory] Vector search failed: {e}\n")

            # 2. FTS5 전문 검색 (키워드 기반)
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
                pass
        finally:
            conn.close()

        return list(results_map.values())[:limit]

    def delete_many(self, project_id: str, keys: list) -> int:
        """주어진 key 리스트에 해당하는 메모리 레코드를 영구 삭제 (FTS 동기화 포함)"""
        if not keys:
            return 0
        conn = get_connection(self.workspace)
        try:
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
            total_row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            total = total_row[0] if total_row else 0
            
            by_cat = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories GROUP BY category"
            ).fetchall()
            
            stats_by_cat = {}
            for r in by_cat:
                cat_name = r[0]
                count = r[1]
                stats_by_cat[cat_name] = count
                
            return {
                "total_memories": total,
                "by_category": stats_by_cat,
            }
        finally:
            conn.close()

    def search_knowledge(self, query: str, category: str = None, limit: int = 10, ve_module=None) -> list:
        """영구 지식 및 전문가 스킬 하이브리드 검색 (FTS5 + sqlite-vec + RRF 스코어링)
        
        Args:
            query: 검색 쿼리
            category: 필터링 카테고리 (선택)
            limit: 최대 결과 수
            ve_module: vector_engine 모듈 (None이면 벡터 검색 생략)
        Returns:
            정렬된 결과 리스트 (key, category, content 200자, score)
        """
        from cortex.search_engine import hybrid_search
        return hybrid_search(self.workspace, query, category, limit, ve_module)


# === 유틸리티 (cortex_mcp.py 등에서 공유) ===

def append_markdown_with_archive(workspace: str, target_filename: str, content: str):
    """마크다운 파일에 내용을 추가하고, 50KB 초과 시 자동 아카이빙"""
    import os
    import datetime
    import shutil
    md_path = os.path.join(workspace, ".agents", "history", target_filename)

    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = os.path.join(workspace, ".agents", "history", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name_part, ext = os.path.splitext(target_filename)
        archive_path = os.path.join(archive_dir, f"{name_part}_{now_str}{ext}")
        shutil.move(md_path, archive_path)

    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(content)
