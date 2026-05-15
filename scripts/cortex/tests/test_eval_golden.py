"""Unit tests for cortex.eval.golden loader."""

import tempfile
import unittest
from pathlib import Path

from cortex.eval.golden import GoldenSetError, load_golden_set


def _write_yaml(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yaml", delete=False
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


class GoldenLoaderTests(unittest.TestCase):
    def test_parses_minimum_valid_case(self):
        path = _write_yaml(
            """
- id: case-1
  query: "hybrid search"
  expected_keys:
    - "scripts/cortex/retrieval/hybrid.py"
"""
        )
        try:
            cases = load_golden_set(path)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].id, "case-1")
            self.assertEqual(cases[0].query, "hybrid search")
            self.assertEqual(cases[0].expected_keys, ("scripts/cortex/retrieval/hybrid.py",))
            self.assertIsNone(cases[0].domain)
            self.assertEqual(cases[0].notes, "")
            self.assertEqual(cases[0].tags, ())
        finally:
            path.unlink()

    def test_parses_all_optional_fields(self):
        path = _write_yaml(
            """
- id: case-2
  query: "edge resolver"
  expected_keys:
    - "scripts/cortex/indexing/edge_resolver.py"
    - "scripts/cortex/indexing/queries.py"
  domain: code
  notes: "Priority 1~4 우선순위 해소"
  tags:
    - retrieval
    - indexing
"""
        )
        try:
            case = load_golden_set(path)[0]
            self.assertEqual(case.domain, "code")
            self.assertEqual(case.notes, "Priority 1~4 우선순위 해소")
            self.assertEqual(case.tags, ("retrieval", "indexing"))
            self.assertEqual(len(case.expected_keys), 2)
        finally:
            path.unlink()

    def test_empty_file_returns_empty_list(self):
        path = _write_yaml("")
        try:
            self.assertEqual(load_golden_set(path), [])
        finally:
            path.unlink()

    def test_missing_required_field_raises(self):
        path = _write_yaml(
            """
- id: case-3
  query: "missing expected_keys"
"""
        )
        try:
            with self.assertRaises(GoldenSetError) as ctx:
                load_golden_set(path)
            self.assertIn("expected_keys", str(ctx.exception))
        finally:
            path.unlink()

    def test_empty_expected_keys_raises(self):
        path = _write_yaml(
            """
- id: case-4
  query: "q"
  expected_keys: []
"""
        )
        try:
            with self.assertRaises(GoldenSetError):
                load_golden_set(path)
        finally:
            path.unlink()

    def test_invalid_domain_raises(self):
        path = _write_yaml(
            """
- id: case-5
  query: "q"
  expected_keys: ["a"]
  domain: nonsense
"""
        )
        try:
            with self.assertRaises(GoldenSetError) as ctx:
                load_golden_set(path)
            self.assertIn("domain", str(ctx.exception))
        finally:
            path.unlink()

    def test_duplicate_id_raises(self):
        path = _write_yaml(
            """
- id: dup
  query: "q1"
  expected_keys: ["a"]
- id: dup
  query: "q2"
  expected_keys: ["b"]
"""
        )
        try:
            with self.assertRaises(GoldenSetError) as ctx:
                load_golden_set(path)
            self.assertIn("dup", str(ctx.exception))
        finally:
            path.unlink()

    def test_top_level_not_a_list_raises(self):
        path = _write_yaml(
            """
id: case-6
query: q
expected_keys: [a]
"""
        )
        try:
            with self.assertRaises(GoldenSetError):
                load_golden_set(path)
        finally:
            path.unlink()

    def test_missing_file_raises(self):
        with self.assertRaises(GoldenSetError):
            load_golden_set("/nonexistent/path/golden.yaml")


if __name__ == "__main__":
    unittest.main()
