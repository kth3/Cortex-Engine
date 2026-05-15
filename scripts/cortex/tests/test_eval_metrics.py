"""Unit tests for cortex.eval.metrics."""

import unittest

from cortex.eval.metrics import hit_at_k, mrr, recall_at_k, aggregate_scores


class HitAtKTests(unittest.TestCase):
    def test_first_position_hits(self):
        self.assertTrue(hit_at_k(["a", "b", "c"], {"a"}, k=1))

    def test_within_k(self):
        self.assertTrue(hit_at_k(["a", "b", "c"], {"c"}, k=3))

    def test_outside_k(self):
        self.assertFalse(hit_at_k(["a", "b", "c"], {"c"}, k=2))

    def test_no_match(self):
        self.assertFalse(hit_at_k(["a", "b", "c"], {"z"}, k=10))

    def test_empty_expected_is_false(self):
        # 정답 정의가 없으면 hit를 주장하지 않는다.
        self.assertFalse(hit_at_k(["a"], set(), k=5))

    def test_zero_k_is_false(self):
        self.assertFalse(hit_at_k(["a", "b"], {"a"}, k=0))

    def test_multiple_expected_any_match(self):
        self.assertTrue(hit_at_k(["a", "b", "c"], {"x", "b"}, k=2))


class MRRTests(unittest.TestCase):
    def test_first_position(self):
        self.assertEqual(mrr(["a", "b", "c"], {"a"}), 1.0)

    def test_second_position(self):
        self.assertAlmostEqual(mrr(["a", "b", "c"], {"b"}), 0.5, places=6)

    def test_third_position(self):
        self.assertAlmostEqual(mrr(["a", "b", "c"], {"c"}), 1.0 / 3.0, places=6)

    def test_no_match(self):
        self.assertEqual(mrr(["a", "b", "c"], {"z"}), 0.0)

    def test_uses_earliest_expected(self):
        # 여러 정답 중 가장 빨리 등장한 것의 rank.
        self.assertEqual(mrr(["a", "b", "c"], {"b", "c"}), 0.5)

    def test_empty_expected(self):
        self.assertEqual(mrr(["a"], set()), 0.0)

    def test_empty_ranked(self):
        self.assertEqual(mrr([], {"a"}), 0.0)


class RecallAtKTests(unittest.TestCase):
    def test_full_recall(self):
        self.assertEqual(recall_at_k(["a", "b", "c"], {"a", "b"}, k=3), 1.0)

    def test_partial_recall(self):
        self.assertEqual(recall_at_k(["a", "b", "c"], {"a", "b"}, k=1), 0.5)

    def test_no_recall(self):
        self.assertEqual(recall_at_k(["a", "b", "c"], {"x", "y"}, k=3), 0.0)

    def test_top_k_truncation(self):
        # 4번째 정답은 top-3에서 빠진다.
        self.assertAlmostEqual(
            recall_at_k(["a", "b", "c", "d"], {"a", "d"}, k=3),
            0.5,
            places=6,
        )

    def test_empty_expected_is_zero(self):
        self.assertEqual(recall_at_k(["a"], set(), k=5), 0.0)

    def test_zero_k_is_zero(self):
        self.assertEqual(recall_at_k(["a"], {"a"}, k=0), 0.0)


class AggregateScoresTests(unittest.TestCase):
    def test_average_per_metric(self):
        out = aggregate_scores([
            {"hit@3": 1.0, "mrr": 1.0},
            {"hit@3": 0.0, "mrr": 0.5},
            {"hit@3": 1.0, "mrr": 0.25},
        ])
        self.assertAlmostEqual(out["hit@3"], 2.0 / 3.0, places=6)
        self.assertAlmostEqual(out["mrr"], (1.0 + 0.5 + 0.25) / 3.0, places=6)

    def test_missing_metric_excluded_from_average(self):
        # mrr가 누락된 케이스는 mrr 모집단에서 빠진다 (0.0으로 채우지 않는다).
        out = aggregate_scores([
            {"hit@3": 1.0, "mrr": 1.0},
            {"hit@3": 0.0},
        ])
        self.assertAlmostEqual(out["hit@3"], 0.5, places=6)
        self.assertAlmostEqual(out["mrr"], 1.0, places=6)

    def test_empty_input_returns_zero_for_requested_metrics(self):
        out = aggregate_scores([], metric_names=["hit@3", "mrr"])
        self.assertEqual(out, {"hit@3": 0.0, "mrr": 0.0})

    def test_empty_input_no_metric_names(self):
        self.assertEqual(aggregate_scores([]), {})

    def test_metric_names_filter(self):
        out = aggregate_scores(
            [{"hit@3": 1.0, "mrr": 0.5, "recall@3": 0.25}],
            metric_names=["hit@3", "mrr"],
        )
        self.assertEqual(set(out.keys()), {"hit@3", "mrr"})


if __name__ == "__main__":
    unittest.main()
