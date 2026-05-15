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

if __name__ == '__main__':
    unittest.main()
