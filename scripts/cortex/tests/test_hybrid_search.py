"""Regression tests for retrieval.hybrid.

분해 리팩토링 진행 전 RRF 결합·sanitize·fallback·도메인 키 셋 계약을 고정한다.
동작 보존이 목적이므로 점수 계산식·정렬 안정성·반환 dict 키 셋을 검증한다.
"""
import unittest
from unittest.mock import patch, MagicMock

from cortex.retrieval import hybrid


def _tuning(snippet_len=200, multiplier=2, rrf_k=60):
    return {
        "search_snippet_len": snippet_len,
        "search_multiplier": multiplier,
        "rrf_k": rrf_k,
    }


def _knowledge_row(key, category="skill", content="body text"):
    return {"key": key, "category": category, "content": content, "tags": []}


class HybridSearchTests(unittest.TestCase):
    """hybrid_search의 RRF·boost 결합 및 sanitize 계약 보호."""

    def setUp(self):
        # patch는 hybrid 모듈 네임스페이스에서 import된 심볼을 갈아끼운다.
        self.patches = [
            patch.object(hybrid, "get_tuning_params", return_value=_tuning(rrf_k=60)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_fts_only_uses_pure_rrf_score(self):
        fts = [_knowledge_row("a"), _knowledge_row("b"), _knowledge_row("c")]
        with patch.object(hybrid, "_fts_search", return_value=fts), \
             patch.object(hybrid, "_vector_search", return_value=[]):
            out = hybrid.hybrid_search("ws", "query", limit=10)

        self.assertEqual([r["key"] for r in out], ["a", "b", "c"])
        # rrf_k=60 → 1/(0+60) ≈ 0.016667, 1/(1+60) ≈ 0.016393, 1/(2+60) ≈ 0.016129
        self.assertAlmostEqual(out[0]["_score_detail"]["rrf"], 1.0 / 60, places=6)
        self.assertAlmostEqual(out[1]["_score_detail"]["rrf"], 1.0 / 61, places=6)
        self.assertAlmostEqual(out[2]["_score_detail"]["rrf"], 1.0 / 62, places=6)

    def test_vector_only_results_use_id_as_key(self):
        vec = [
            {"id": "v1", "text": "vector1", "meta": {"category": "skill"}},
            {"id": "v2", "text": "vector2", "meta": {"category": "skill"}},
        ]
        with patch.object(hybrid, "_fts_search", return_value=[]), \
             patch.object(hybrid, "_vector_search", return_value=vec):
            out = hybrid.hybrid_search("ws", "query", limit=10)

        self.assertEqual([r["key"] for r in out], ["v1", "v2"])
        # vector-only 경로는 content를 snippet으로 채워야 한다
        self.assertIn("content", out[0])

    def test_fts_and_vector_same_key_scores_sum(self):
        fts = [_knowledge_row("shared"), _knowledge_row("only_fts")]
        vec = [
            {"id": "shared", "text": "vector body", "meta": {"category": "skill"}},
            {"id": "only_vec", "text": "vec body", "meta": {"category": "skill"}},
        ]
        with patch.object(hybrid, "_fts_search", return_value=fts), \
             patch.object(hybrid, "_vector_search", return_value=vec):
            out = hybrid.hybrid_search("ws", "shared", limit=10)

        by_key = {r["key"]: r for r in out}
        self.assertIn("shared", by_key)
        self.assertIn("only_fts", by_key)
        self.assertIn("only_vec", by_key)
        # shared의 rrf 부분은 두 rrf 점수의 합
        expected = 1.0 / 60 + 1.0 / 60
        self.assertAlmostEqual(by_key["shared"]["_score_detail"]["rrf"], expected, places=6)
        # only_fts/only_vec는 단독 rrf만
        self.assertAlmostEqual(by_key["only_fts"]["_score_detail"]["rrf"], 1.0 / 61, places=6)
        self.assertAlmostEqual(by_key["only_vec"]["_score_detail"]["rrf"], 1.0 / 61, places=6)

    def test_surrogate_query_does_not_raise(self):
        # lone surrogate는 utf-8 인코딩 시 에러를 일으키지만, sanitize가 replace로 처리해야 한다
        bad_query = "valid \ud83d tail"
        with patch.object(hybrid, "_fts_search", return_value=[]) as fts_mock, \
             patch.object(hybrid, "_vector_search", return_value=[]):
            out = hybrid.hybrid_search("ws", bad_query, limit=5)

        self.assertEqual(out, [])
        # _fts_search에 전달된 query에는 lone surrogate가 그대로 남아 있지 않아야 한다
        called_query = fts_mock.call_args[0][1]
        self.assertNotIn("\ud83d", called_query)

    def test_category_is_lowercased_before_search(self):
        with patch.object(hybrid, "_fts_search", return_value=[]) as fts_mock, \
             patch.object(hybrid, "_vector_search", return_value=[]) as vec_mock:
            hybrid.hybrid_search("ws", "q", category="SKILL", limit=5)

        self.assertEqual(fts_mock.call_args[0][2], "skill")
        self.assertEqual(vec_mock.call_args[0][2], "skill")

    def test_total_score_rounds_to_six_places(self):
        fts = [_knowledge_row("k")]
        with patch.object(hybrid, "_fts_search", return_value=fts), \
             patch.object(hybrid, "_vector_search", return_value=[]):
            out = hybrid.hybrid_search("ws", "q", limit=1)

        # round(..., 6) — 7번째 소수점 자릿수 이하의 차이가 없어야 한다
        total = out[0]["_total_score"]
        self.assertEqual(round(total, 6), total)

    def test_boost_applies_for_exact_key_match(self):
        # _heuristic_boost는 item_key == query 이면 +0.5
        fts = [_knowledge_row("exact-match", category="skill"), _knowledge_row("other")]
        with patch.object(hybrid, "_fts_search", return_value=fts), \
             patch.object(hybrid, "_vector_search", return_value=[]):
            out = hybrid.hybrid_search("ws", "exact-match", limit=10)

        # exact는 rrf 점수가 더 낮더라도 boost로 1위 유지
        self.assertEqual(out[0]["key"], "exact-match")
        # boost 필드가 표기되어야 한다
        self.assertIn("boost", out[0]["_score_detail"])
        self.assertGreater(out[0]["_score_detail"]["boost"], 0.0)


class UnifiedPipelineSearchTests(unittest.TestCase):
    """unified_pipeline_search의 sanitize·fallback·conn 라이프사이클 보호."""

    def _make_mock_conn(self, exec_side_effect):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = exec_side_effect
        return mock_conn

    def _empty_cursor(self):
        cur = MagicMock()
        cur.fetchall.return_value = []
        return cur

    def test_runs_without_ve_module(self):
        """ve_module=None이어도 observation/FTS fallback 경로로 결과 생성 또는 빈 리스트."""
        def exec_side_effect(sql, params=None):
            return self._empty_cursor()

        mock_conn = self._make_mock_conn(exec_side_effect)
        with patch.object(hybrid, "get_tuning_params", return_value=_tuning()), \
             patch.object(hybrid, "get_connection", return_value=mock_conn), \
             patch.object(hybrid, "_fts_search", return_value=[]), \
             patch("cortex.db.search_nodes_fts", return_value=[]):
            out = hybrid.unified_pipeline_search("ws", "query", limit=5, ve_module=None)

        self.assertEqual(out, [])
        mock_conn.close.assert_called_once()

    def test_surrogate_query_is_sanitized(self):
        def exec_side_effect(sql, params=None):
            return self._empty_cursor()

        mock_conn = self._make_mock_conn(exec_side_effect)
        with patch.object(hybrid, "get_tuning_params", return_value=_tuning()), \
             patch.object(hybrid, "get_connection", return_value=mock_conn), \
             patch.object(hybrid, "_fts_search", return_value=[]) as fts_mock, \
             patch("cortex.db.search_nodes_fts", return_value=[]):
            hybrid.unified_pipeline_search("ws", "bad \ud83d tail", limit=5, ve_module=None)

        # _fts_search에 전달된 쿼리에 lone surrogate가 없어야 한다
        self.assertNotIn("\ud83d", fts_mock.call_args[0][1])

    def test_vector_failure_falls_back_to_fts(self):
        """ve_module이 예외를 던지면 FTS fallback으로 결과를 보존해야 한다."""
        ve_module = MagicMock()
        ve_module.get_embeddings.side_effect = RuntimeError("embedding server offline")

        def exec_side_effect(sql, params=None):
            return self._empty_cursor()

        mock_conn = self._make_mock_conn(exec_side_effect)
        fts_mems = [
            {"key": "fallback-mem", "category": "skill", "content": "fb body", "tags": []},
        ]

        with patch.object(hybrid, "get_tuning_params", return_value=_tuning()), \
             patch.object(hybrid, "get_connection", return_value=mock_conn), \
             patch.object(hybrid, "_fts_search", return_value=fts_mems), \
             patch("cortex.db.search_nodes_fts", return_value=[]), \
             patch("cortex.embeddings.hardware.detect_gpu", return_value=False, create=True):
            out = hybrid.unified_pipeline_search("ws", "q", limit=5, ve_module=ve_module)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["domain"], "knowledge")
        self.assertEqual(out[0]["key"], "fallback-mem")
        mock_conn.close.assert_called_once()

    def test_result_contract_keys_per_domain(self):
        """code/knowledge/observation 도메인 각각이 계약된 키 셋을 유지해야 한다."""
        def exec_side_effect(sql, params=None):
            cur = MagicMock()
            if "FROM observations" in sql:
                cur.fetchall.return_value = [
                    {"id": 7, "content": "obs body", "type": "observation"},
                ]
            else:
                cur.fetchall.return_value = []
            return cur

        mock_conn = self._make_mock_conn(exec_side_effect)
        fts_mems = [
            {"key": "mem-1", "category": "skill", "content": "mem body", "tags": []},
        ]
        fts_nodes = [
            {"fqn": "pkg.mod.fn", "type": "function", "file_path": "pkg/mod.py", "signature": "def fn()"},
        ]

        with patch.object(hybrid, "get_tuning_params", return_value=_tuning()), \
             patch.object(hybrid, "get_connection", return_value=mock_conn), \
             patch.object(hybrid, "_fts_search", return_value=fts_mems), \
             patch("cortex.db.search_nodes_fts", return_value=fts_nodes):
            out = hybrid.unified_pipeline_search("ws", "q", limit=10, ve_module=None)

        domains = {r["domain"]: r for r in out}
        self.assertIn("knowledge", domains)
        self.assertIn("observation", domains)
        self.assertIn("code", domains)

        code = domains["code"]
        self.assertEqual(set(code.keys()), {"domain", "key", "category", "file_path", "snippet", "_total_score"})
        self.assertEqual(code["key"], "pkg.mod.fn")

        knowledge = domains["knowledge"]
        self.assertEqual(set(knowledge.keys()), {"domain", "key", "category", "snippet", "_total_score"})

        observation = domains["observation"]
        self.assertEqual(set(observation.keys()), {"domain", "key", "category", "snippet", "_total_score"})
        self.assertEqual(observation["key"], "7")  # id가 str로 변환되어야 한다

    def test_connection_closed_even_when_node_fts_fails(self):
        """search_nodes_fts 예외에서도 finally에서 conn.close()가 호출되어야 한다."""
        def exec_side_effect(sql, params=None):
            return self._empty_cursor()

        mock_conn = self._make_mock_conn(exec_side_effect)
        with patch.object(hybrid, "get_tuning_params", return_value=_tuning()), \
             patch.object(hybrid, "get_connection", return_value=mock_conn), \
             patch.object(hybrid, "_fts_search", return_value=[]), \
             patch("cortex.db.search_nodes_fts", side_effect=RuntimeError("node fts broken")):
            hybrid.unified_pipeline_search("ws", "q", limit=5, ve_module=None)

        mock_conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
