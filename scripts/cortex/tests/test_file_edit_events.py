"""file_edit_events 스키마/헬퍼 검증 — Stage 0 T1~T9.

운영 DB(memories.db)를 건드리지 않도록 모든 테스트는 tempfile + 격리된 워크스페이스 사용.
실행: uv run --project .cortex python .cortex/scripts/cortex/tests/test_file_edit_events.py
"""
import os
import sys
import tempfile
import unittest
import sqlite3
from pathlib import Path

# 스크립트 루트를 sys.path에 추가하여 cortex 패키지 import
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import storage as db
from cortex.editing.engine import (
    EMPTY_FILE_HASH,
    ALLOWED_SOURCES,
    normalize_event_path,
    canonical_sources,
    upsert_edit_event,
    record_edit_event,
)


def _new_workspace_with_db():
    """임시 워크스페이스 + memories.db 초기화 후 (workspace, conn) 반환."""
    tmpdir = tempfile.mkdtemp(prefix="fee_test_")
    
    # 격리된 환경을 위해 CORTEX_HOME 환경 변수 설정
    old_cortex_home = os.environ.get("CORTEX_HOME")
    os.environ["CORTEX_HOME"] = os.path.join(tmpdir, ".cortex")
    
    try:
        # cortex storage.get_db_path는 .cortex 자동 감지 → 임시 워크스페이스에 .cortex/data 생성
        conn = db.get_connection(tmpdir)
        db.init_schema(conn)
    finally:
        # 환경 변수 복원
        if old_cortex_home is not None:
            os.environ["CORTEX_HOME"] = old_cortex_home
        else:
            del os.environ["CORTEX_HOME"]
            
    return tmpdir, conn


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


class T1SchemaMeta(unittest.TestCase):
    """T1: 테이블/인덱스 메타 확인."""

    def test_table_columns(self):
        ws, conn = _new_workspace_with_db()
        try:
            rows = conn.execute("PRAGMA table_info(file_edit_events)").fetchall()
            cols = {r[1]: (r[2], r[3]) for r in rows}  # name -> (type, notnull)
            expected = {
                "id": ("INTEGER", 0),
                "file_path": ("TEXT", 1),
                "before_hash": ("TEXT", 1),
                "after_hash": ("TEXT", 1),
                "line_range": ("TEXT", 0),
                "tool_name": ("TEXT", 0),
                "event_sources": ("TEXT", 1),
                "session_id": ("TEXT", 1),
                "edit_summary": ("TEXT", 0),
                "created_at": ("TEXT", 1),
                "updated_at": ("TEXT", 1),
            }
            for name, (typ, notnull) in expected.items():
                self.assertIn(name, cols, f"column {name} missing")
                self.assertEqual(cols[name][0].upper(), typ, f"column {name} type")
                self.assertEqual(cols[name][1], notnull, f"column {name} notnull")
        finally:
            conn.close()

    def test_indexes_and_unique(self):
        ws, conn = _new_workspace_with_db()
        try:
            idx_rows = conn.execute("PRAGMA index_list(file_edit_events)").fetchall()
            names = {r[1] for r in idx_rows}
            self.assertIn("idx_fee_path_updated", names)
            self.assertIn("idx_fee_session_updated", names)
            # UNIQUE 제약 확인
            unique_indexes = [r[1] for r in idx_rows if r[2] == 1]
            found_unique = False
            for uname in unique_indexes:
                info = conn.execute(f"PRAGMA index_info({uname})").fetchall()
                cols = [r[2] for r in info]
                if cols == ["file_path", "before_hash", "after_hash", "session_id"]:
                    found_unique = True
                    break
            self.assertTrue(found_unique, "UNIQUE(file_path, before_hash, after_hash, session_id) 인덱스 없음")
        finally:
            conn.close()


class T2InsertSelectRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        ws, conn = _new_workspace_with_db()
        try:
            upsert_edit_event(
                conn,
                file_path="src/foo.py",
                before_hash=_hash("a"),
                after_hash=_hash("b"),
                session_id="sess-1",
                event_source="cortex_mcp",
                tool_name="Edit",
                line_range="10-12",
                edit_summary="rename foo to bar",
                now_iso="2026-05-09T00:00:00Z",
            )
            row = conn.execute(
                "SELECT * FROM file_edit_events WHERE session_id=?", ("sess-1",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["file_path"], "src/foo.py")
            self.assertEqual(row["before_hash"], _hash("a"))
            self.assertEqual(row["after_hash"], _hash("b"))
            self.assertEqual(row["line_range"], "10-12")
            self.assertEqual(row["tool_name"], "Edit")
            self.assertEqual(row["event_sources"], "cortex_mcp")
            self.assertEqual(row["edit_summary"], "rename foo to bar")
            self.assertEqual(row["created_at"], "2026-05-09T00:00:00Z")
            self.assertEqual(row["updated_at"], "2026-05-09T00:00:00Z")
        finally:
            conn.close()


class T3NullRejection(unittest.TestCase):
    def _expect_integrity_error(self, conn, **overrides):
        defaults = dict(
            file_path="src/x.py",
            before_hash=_hash("a"),
            after_hash=_hash("b"),
            line_range=None,
            tool_name=None,
            event_sources="cortex_mcp",
            session_id="sess-null",
            edit_summary=None,
            created_at="2026-05-09T00:00:00Z",
            updated_at="2026-05-09T00:00:00Z",
        )
        defaults.update(overrides)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO file_edit_events
                   (file_path, before_hash, after_hash, line_range, tool_name,
                    event_sources, session_id, edit_summary, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    defaults["file_path"], defaults["before_hash"], defaults["after_hash"],
                    defaults["line_range"], defaults["tool_name"], defaults["event_sources"],
                    defaults["session_id"], defaults["edit_summary"],
                    defaults["created_at"], defaults["updated_at"],
                ),
            )

    def test_null_before_hash(self):
        ws, conn = _new_workspace_with_db()
        try:
            self._expect_integrity_error(conn, before_hash=None)
        finally:
            conn.close()

    def test_null_after_hash(self):
        ws, conn = _new_workspace_with_db()
        try:
            self._expect_integrity_error(conn, after_hash=None)
        finally:
            conn.close()

    def test_null_session_id(self):
        ws, conn = _new_workspace_with_db()
        try:
            self._expect_integrity_error(conn, session_id=None)
        finally:
            conn.close()


class T4UpsertAccumulate(unittest.TestCase):
    """cortex_mcp 반복 호출 시 event_sources='cortex_mcp'로 중복 차단."""

    def test_accumulate_canonical(self):
        ws, conn = _new_workspace_with_db()
        try:
            kwargs = dict(
                file_path="src/x.py",
                before_hash=_hash("a"),
                after_hash=_hash("b"),
                session_id="sess-T4",
                tool_name="Edit",
            )
            upsert_edit_event(conn, event_source="cortex_mcp",
                              now_iso="2026-05-09T00:00:01Z", **kwargs)
            upsert_edit_event(conn, event_source="cortex_mcp",
                              now_iso="2026-05-09T00:00:02Z", **kwargs)
            upsert_edit_event(conn, event_source="cortex_mcp",
                              now_iso="2026-05-09T00:00:03Z", **kwargs)
            rows = conn.execute(
                "SELECT event_sources FROM file_edit_events WHERE session_id=?",
                ("sess-T4",)
            ).fetchall()
            self.assertEqual(len(rows), 1, "dedup 키로 1 row 유지되어야 함")
            self.assertEqual(rows[0]["event_sources"], "cortex_mcp",
                             "canonical 정렬 + 중복 차단")
        finally:
            conn.close()

    def test_unknown_source_guard(self):
        with self.assertRaises(ValueError):
            canonical_sources(None, "bogus_source")
        with self.assertRaises(ValueError):
            canonical_sources("legacy_harness,evil_source", "cortex_mcp")


class T5CreatedAtPreserved(unittest.TestCase):
    def test_created_at_immutable(self):
        ws, conn = _new_workspace_with_db()
        try:
            kwargs = dict(
                file_path="src/y.py",
                before_hash=_hash("a"),
                after_hash=_hash("b"),
                session_id="sess-T5",
                tool_name="Edit",
            )
            upsert_edit_event(conn, event_source="cortex_mcp",
                              now_iso="2026-05-09T00:00:01Z", **kwargs)
            upsert_edit_event(conn, event_source="cortex_mcp",
                              now_iso="2026-05-09T01:00:00Z", **kwargs)
            row = conn.execute(
                "SELECT created_at, updated_at FROM file_edit_events WHERE session_id=?",
                ("sess-T5",)
            ).fetchone()
            self.assertEqual(row["created_at"], "2026-05-09T00:00:01Z",
                             "created_at은 첫 INSERT 값 보존")
            self.assertEqual(row["updated_at"], "2026-05-09T01:00:00Z",
                             "updated_at만 갱신")
        finally:
            conn.close()


class T6PathNormalization(unittest.TestCase):
    """절대/상대/슬래시/Windows 케이스 변형 4종이 동일 정규화 결과 반환."""

    def test_four_variants_unify(self):
        ws_dir = tempfile.mkdtemp(prefix="fee_norm_")
        try:
            target = os.path.join(ws_dir, "Src", "Foo.py")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as f:
                f.write("# stub\n")
            # 4종 변형
            variants = [
                target,                                     # 절대 경로
                os.path.join("Src", "Foo.py"),              # 상대 경로
                "Src/Foo.py",                               # forward slash
                "src/foo.py",                               # 케이스 변형 (Windows에서만 동등)
            ]
            results = [normalize_event_path(ws_dir, v) for v in variants]
            # Windows에서는 모두 'src/foo.py'(lower), Unix에서는 마지막만 다름
            if os.name == "nt":
                self.assertEqual(len(set(results)), 1,
                                 f"Windows에서 4종 변형 통합 실패: {results}")
                self.assertEqual(results[0], "src/foo.py")
            else:
                # Unix: 처음 3개는 'Src/Foo.py', 마지막 'src/foo.py'는 다른 파일
                self.assertEqual(results[0], "Src/Foo.py")
                self.assertEqual(results[1], "Src/Foo.py")
                self.assertEqual(results[2], "Src/Foo.py")
        finally:
            import shutil
            shutil.rmtree(ws_dir, ignore_errors=True)


class T7ExternalPathRejection(unittest.TestCase):
    def test_dotdot_rejected(self):
        ws_dir = tempfile.mkdtemp(prefix="fee_ext_")
        try:
            # 워크스페이스 외부 경로
            external = os.path.join(ws_dir, "..", "outside.py")
            result = normalize_event_path(ws_dir, external)
            self.assertIsNone(result, "워크스페이스 외부 경로는 None 반환")
        finally:
            import shutil
            shutil.rmtree(ws_dir, ignore_errors=True)


class T8FileLineagePreservation(unittest.TestCase):
    """init_schema 재호출 시 기존 file_lineage row가 보존되는지."""

    def test_existing_row_preserved(self):
        ws, conn = _new_workspace_with_db()
        try:
            conn.execute(
                "INSERT INTO file_lineage(file_path, commit_count, churn_score, "
                "last_author, last_commit_ts, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("src/lineage.py", 5, 0.5, "alice", 1000, 2000),
            )
            conn.commit()
            # init_schema 재호출 (마이그레이션 시뮬레이션)
            db.init_schema(conn)
            row = conn.execute(
                "SELECT * FROM file_lineage WHERE file_path=?",
                ("src/lineage.py",)
            ).fetchone()
            self.assertIsNotNone(row, "init_schema 재호출 후 기존 row 보존")
            self.assertEqual(row["commit_count"], 5)
            self.assertEqual(row["last_author"], "alice")
        finally:
            conn.close()


class T9GetStatsAfterMigration(unittest.TestCase):
    def test_get_stats_works(self):
        ws, conn = _new_workspace_with_db()
        try:
            stats = db.get_stats(conn)
            self.assertIn("total_nodes", stats)
            self.assertIn("schema_version", stats)
            self.assertEqual(stats["schema_version"], "2",
                             "schema_version은 v1→v2 마이그레이션 후 '2'")
        finally:
            conn.close()


class T10RecordEditEvent(unittest.TestCase):
    """MCP 성공 경로가 실제 파일 전체 내용 해시를 남기는지 검증."""

    def test_record_edit_event_uses_full_file_hashes(self):
        ws, conn = _new_workspace_with_db()
        try:
            before = "alpha\nneedle\nomega\n"
            after = "alpha\nchanged\nomega\n"
            record_edit_event(
                conn,
                workspace=ws,
                file_path="Src/Foo.py",
                before_content=before,
                after_content=after,
                session_id="sess-T10",
                event_source="cortex_mcp",
                tool_name="pc_strict_replace",
                edit_summary="smoke strict replace",
                now_iso="2026-05-09T02:00:00Z",
            )
            row = conn.execute(
                "SELECT * FROM file_edit_events WHERE session_id=?",
                ("sess-T10",)
            ).fetchone()
            self.assertIsNotNone(row)
            expected_path = "src/foo.py" if os.name == "nt" else "Src/Foo.py"
            self.assertEqual(row["file_path"], expected_path)
            self.assertEqual(row["before_hash"], _hash(before))
            self.assertEqual(row["after_hash"], _hash(after))
            self.assertEqual(row["event_sources"], "cortex_mcp")
            self.assertEqual(row["tool_name"], "pc_strict_replace")
            self.assertEqual(row["edit_summary"], "smoke strict replace")
        finally:
            conn.close()


def run():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [T1SchemaMeta, T2InsertSelectRoundtrip, T3NullRejection,
                T4UpsertAccumulate, T5CreatedAtPreserved, T6PathNormalization,
                T7ExternalPathRejection, T8FileLineagePreservation,
                T9GetStatsAfterMigration, T10RecordEditEvent]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
