import unittest

from cortex.retrieval.snippets import (
    CODE_LOCATION_FALLBACK,
    code_result_snippet,
    normalize_snippet_text,
    result_snippet,
    source_location,
    text_result_snippet,
    truncate_snippet,
)


class RetrievalSnippetTests(unittest.TestCase):
    def test_normalize_snippet_text_collapses_whitespace(self):
        self.assertEqual(
            normalize_snippet_text("def foo():\n    return 1"),
            "def foo(): return 1",
        )

    def test_truncate_snippet_adds_ellipsis(self):
        self.assertEqual(truncate_snippet("abcdef", max_chars=4), "abc…")

    def test_code_snippet_prefers_signature(self):
        row = {
            "signature": "def run_pipeline(query: str) -> dict:",
            "content": "less useful body",
            "file_path": "scripts/cortex/retrieval/hybrid.py",
        }
        self.assertEqual(
            code_result_snippet(row),
            "def run_pipeline(query: str) -> dict:",
        )

    def test_code_snippet_uses_content_when_signature_missing(self):
        row = {
            "content": "class PlayerController:\n    pass",
            "file_path": "Assets/Scripts/PlayerController.cs",
        }
        self.assertEqual(
            code_result_snippet(row),
            "class PlayerController: pass",
        )

    def test_code_snippet_uses_location_before_static_fallback(self):
        row = {
            "file_path": "scripts/cortex/retrieval/hybrid.py",
            "line": 120,
        }
        self.assertEqual(
            code_result_snippet(row),
            "→ scripts/cortex/retrieval/hybrid.py:120 참조 (코드 본문 생략됨)",
        )

    def test_code_snippet_static_fallback(self):
        self.assertEqual(code_result_snippet({}), CODE_LOCATION_FALLBACK)

    def test_text_snippet_prefers_existing_snippet(self):
        row = {
            "snippet": "existing summary",
            "content": "full content",
        }
        self.assertEqual(text_result_snippet(row), "existing summary")

    def test_text_snippet_uses_observation(self):
        row = {"observation": "decision recorded"}
        self.assertEqual(text_result_snippet(row), "decision recorded")

    def test_result_snippet_dispatches_code_domain(self):
        row = {"signature": "def foo() -> None:"}
        self.assertEqual(
            result_snippet(row, domain="code"),
            "def foo() -> None:",
        )

    def test_source_location_uses_fqn_and_line(self):
        row = {"fqn": "cortex.retrieval.hybrid.unified_pipeline_search", "start_line": 33}
        self.assertEqual(
            source_location(row),
            "cortex.retrieval.hybrid.unified_pipeline_search:33",
        )


if __name__ == "__main__":
    unittest.main()
