import unittest
import sqlite3
from cortex.retrieval.fts_query import normalize_fts_query, escape_fts_phrase

class TestFTSQueryNormalization(unittest.TestCase):
    def test_normalize_empty_strings(self):
        self.assertEqual(normalize_fts_query(""), "")
        self.assertEqual(normalize_fts_query("   "), "")
        self.assertEqual(normalize_fts_query(None), "")

    def test_normalize_file_paths(self):
        self.assertEqual(
            normalize_fts_query("scripts/cortex/retrieval/fts.py"),
            '"scripts/cortex/retrieval/fts.py"*'
        )

    def test_normalize_camel_case(self):
        self.assertEqual(
            normalize_fts_query("some_camel_case_Func"),
            '"some_camel_case_Func"*'
        )

    def test_normalize_multiple_words(self):
        self.assertEqual(
            normalize_fts_query("Unity PlayerController"),
            '"Unity"* OR "PlayerController"*'
        )

    def test_escape_fts_phrase(self):
        self.assertEqual(escape_fts_phrase('a"b'), 'a""b')

    def test_normalize_with_quotes(self):
        self.assertEqual(
            normalize_fts_query('quote "inside"'),
            '"quote"* OR """inside"""*'
        )

    def test_sqlite_fts5_execution(self):
        """인메모리 SQLite DB에 FTS5 가상 테이블을 생성하고, 변환된 쿼리가 실제 MATCH 절에서 에러를 일으키지 않는지 확인"""
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE fts_test USING fts5(content);")
        except sqlite3.OperationalError as e:
            self.skipTest(f"FTS5 not supported in this SQLite environment: {e}")
            return

        test_cases = [
            "scripts/cortex/retrieval/fts.py",
            "some_camel_case_Func",
            "MyClass",
            "my_func",
            "Unity PlayerController",
            "path with spaces",
            'quote "inside"',
            "",
            "   ",
        ]

        for query in test_cases:
            safe_query = normalize_fts_query(query)
            if not safe_query:
                continue
            
            try:
                # MATCH 쿼리 실행 자체가 성공하는지(OperationalError 미발생) 확인
                conn.execute("SELECT * FROM fts_test WHERE fts_test MATCH ?", (safe_query,)).fetchall()
            except sqlite3.OperationalError as e:
                self.fail(f"OperationalError for query {query!r} -> {safe_query!r}: {e}")
        conn.close()

if __name__ == '__main__':
    unittest.main()
