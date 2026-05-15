import sqlite3
import unittest
from cortex.storage.schema import init_schema
from cortex.storage.migrations import _apply_migrations
from cortex.indexing.records import insert_edges
from cortex.indexing.edge_resolver import resolve_unresolved_edges

class TestEdgePersistenceResolver(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_migration_adds_edge_hint_columns(self):
        c = sqlite3.connect(":memory:")
        c.executescript("""
            CREATE TABLE meta(key TEXT, value TEXT);
            CREATE TABLE file_cache(file_path TEXT PRIMARY KEY, hash TEXT NOT NULL, last_indexed_at INTEGER NOT NULL, node_count INTEGER DEFAULT 0);
            CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, name TEXT, fqn TEXT, file_path TEXT, start_line INTEGER, end_line INTEGER, language TEXT);
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'CALLS',
                call_site_line INTEGER,
                confidence REAL DEFAULT 1.0,
                UNIQUE(source_id, target_id, type)
            );
        """)
        _apply_migrations(c)
        
        edge_cols_info = c.execute("PRAGMA table_info(edges)").fetchall()
        edge_columns = [col[1] for col in edge_cols_info]
        
        self.assertIn("target_name", edge_columns)
        self.assertIn("target_kind_hint", edge_columns)
        self.assertIn("target_fqn_hint", edge_columns)
        self.assertIn("resolution_status", edge_columns)
        self.assertIn("resolution_confidence", edge_columns)

    def test_insert_edges_persistence(self):
        edges = [{
            "source_id": "node1",
            "target_id": "node2",
            "type": "CALLS",
            "target_name": "my_func",
            "target_kind_hint": "function",
            "target_fqn_hint": "my_module.my_func",
            "call_site_line": 42,
            "confidence": 0.95
        }]
        insert_edges(self.conn, edges)
        row = self.conn.execute("SELECT source_id, target_id, type, target_name, target_kind_hint, target_fqn_hint, resolution_status, resolution_confidence, call_site_line, confidence FROM edges WHERE source_id = 'node1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[3], "my_func")
        self.assertEqual(row[4], "function")
        self.assertEqual(row[5], "my_module.my_func")
        self.assertEqual(row[6], "resolved")
        self.assertEqual(row[8], 42)
        self.assertEqual(row[9], 0.95)

    def test_insert_edges_unresolved(self):
        edges = [{
            "source_id": "node1",
            "target_id": "__unresolved__::my_func",
            "type": "CALLS",
            "target_name": "my_func"
        }]
        insert_edges(self.conn, edges)
        row = self.conn.execute("SELECT resolution_status FROM edges WHERE source_id = 'node1'").fetchone()
        self.assertEqual(row[0], "unresolved")

    def test_resolver_with_target_kind_hint(self):
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_class', 'class', 'Target', 'Target', 'tgt.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_func', 'function', 'Target', 'Target', 'tgt.py', 2, 2, 'python')")

        edges = [{"source_id": "src", "target_id": "__unresolved__::Target", "type": "CALLS", "target_kind_hint": "class"}]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt_class")
        self.assertEqual(row[1], "resolved")

    def test_resolver_ambiguous(self):
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_func1', 'function', 'Target', 'm1.Target', 'tgt1.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_func2', 'function', 'Target', 'm2.Target', 'tgt2.py', 1, 1, 'python')")

        edges = [{"source_id": "src", "target_id": "__unresolved__::Target", "type": "CALLS", "target_kind_hint": "function"}]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "__unresolved__::Target")
        self.assertEqual(row[1], "ambiguous")

    def test_resolver_python_fqn(self):
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt', 'class', 'MyClass', 'some/module.py::MyClass', 'module.py', 1, 1, 'python')")

        edges = [{"source_id": "src", "target_id": "__unresolved_fqn__::some.module.MyClass", "type": "CALLS"}]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt")
        self.assertEqual(row[1], "resolved")

    def test_resolver_csharp_monobehaviour(self):
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'class', 'Player', 'Player', 'player.cs', 1, 1, 'c_sharp')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt', 'class', 'MonoBehaviour', 'UnityEngine.MonoBehaviour', 'unity.cs', 1, 1, 'c_sharp')")

        edges = [{"source_id": "src", "target_id": "__unresolved__::MonoBehaviour", "type": "INHERITS", "target_kind_hint": "class"}]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt")
        self.assertEqual(row[1], "resolved")

    def test_resolver_priority1_fqn_hint_exact_match(self):
        # Priority 1: target_fqn_hint가 nodes.fqn과 정확히 일치하는 후보 1개
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")
        # 같은 name을 가진 다른 후보가 있어도 fqn_hint가 일치하는 노드를 우선 선택해야 한다
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_other', 'function', 'helper', 'other.module.helper', 'other.py', 1, 1, 'python')")
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_hit', 'function', 'helper', 'pkg.mod.helper', 'pkg/mod.py', 1, 1, 'python')")

        edges = [{
            "source_id": "src",
            "target_id": "__unresolved__::helper",
            "type": "CALLS",
            "target_name": "helper",
            "target_fqn_hint": "pkg.mod.helper",
        }]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt_hit")
        self.assertEqual(row[1], "resolved")

    def test_resolver_priority3_language_only(self):
        # Priority 3: target_kind_hint가 없거나 어떤 후보와도 매칭되지 않을 때,
        # source_lang과 일치하는 후보가 1개면 단일 매칭으로 resolve
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")
        # python language + name 일치 후보 1개
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_py', 'function', 'shared_name', 'pkg.shared_name', 'pkg.py', 1, 1, 'python')")
        # 다른 language의 동일 name 후보는 매칭 대상이 아니어야 한다
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_cs', 'function', 'shared_name', 'shared_name', 'a.cs', 1, 1, 'c_sharp')")

        edges = [{
            "source_id": "src",
            "target_id": "__unresolved__::shared_name",
            "type": "CALLS",
            "target_name": "shared_name",
            # kind_hint를 일부러 어떤 후보와도 맞지 않게 두어 Priority 2가 비도록 한다
            "target_kind_hint": "class",
        }]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt_py")
        self.assertEqual(row[1], "resolved")

    def test_resolver_priority4_name_only_when_no_source_language(self):
        # Priority 4: source 노드의 language가 비어 있어 Priority 2/3 모두 비면 name 매칭으로 fallback
        # _source_language_map은 nodes 테이블 JOIN으로 얻으므로 src 노드를 등록하지 않아 language가 없는 상태를 만든다.
        # 단, edges는 외래키 검증을 하지 않으므로 src_id 'src_missing'은 단순 문자열로 사용 가능.
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('tgt_only', 'function', 'unique_name', 'mod.unique_name', 'mod.py', 1, 1, 'python')")

        edges = [{
            "source_id": "src_missing",
            "target_id": "__unresolved__::unique_name",
            "type": "CALLS",
            "target_name": "unique_name",
        }]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "tgt_only")
        self.assertEqual(row[1], "resolved")

    def test_resolver_no_match_keeps_unresolved(self):
        # 후보 0개: target_id와 resolution_status는 변경되지 않아야 한다
        self.conn.execute("INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, language) VALUES ('src', 'function', 'src_func', 'src', 'src.py', 1, 1, 'python')")

        edges = [{
            "source_id": "src",
            "target_id": "__unresolved__::nonexistent_symbol",
            "type": "CALLS",
            "target_name": "nonexistent_symbol",
        }]
        insert_edges(self.conn, edges)
        resolve_unresolved_edges(self.conn)
        row = self.conn.execute("SELECT target_id, resolution_status FROM edges").fetchone()
        self.assertEqual(row[0], "__unresolved__::nonexistent_symbol")
        self.assertEqual(row[1], "unresolved")

if __name__ == '__main__':
    unittest.main()
