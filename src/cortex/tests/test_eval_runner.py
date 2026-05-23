"""End-to-end test for the evaluation runner.

저장소 동봉 fixture·골든셋을 실제로 인덱싱·검색·평가하여 결과 구조와
최소 품질을 검증한다. 사용자 워크스페이스에 의존하지 않는다.
"""

import unittest

from cortex.eval.runner import DEFAULT_K_VALUES, evaluate


REQUIRED_AGGREGATE_METRICS = (
    "mrr",
    "hit@1",
    "hit@3",
    "hit@5",
    "recall@1",
    "recall@3",
    "recall@5",
)


class EvaluateE2ETests(unittest.TestCase):
    def test_returns_aggregate_and_cases(self):
        result = evaluate()
        self.assertIn("aggregate", result)
        self.assertIn("cases", result)
        self.assertIn("total_cases", result)
        self.assertIn("k_values", result)
        self.assertEqual(result["k_values"], list(DEFAULT_K_VALUES))
        self.assertGreater(result["total_cases"], 0)
        self.assertEqual(len(result["cases"]), result["total_cases"])

    def test_aggregate_contains_all_default_metrics(self):
        result = evaluate()
        for metric in REQUIRED_AGGREGATE_METRICS:
            self.assertIn(metric, result["aggregate"], f"누락된 metric: {metric}")
            value = result["aggregate"][metric]
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_each_case_has_required_fields(self):
        result = evaluate(k_values=(1, 3))
        for case in result["cases"]:
            self.assertIn("id", case)
            self.assertIn("query", case)
            self.assertIn("expected", case)
            self.assertIn("ranked", case)
            self.assertIn("scores", case)
            self.assertIn("mrr", case["scores"])
            self.assertIn("hit@1", case["scores"])
            self.assertIn("hit@3", case["scores"])
            # ranked는 top-k만 잘려서 들어옴
            self.assertLessEqual(len(case["ranked"]), 3)

    def test_custom_k_values_propagate(self):
        result = evaluate(k_values=(2, 10))
        self.assertEqual(result["k_values"], [2, 10])
        self.assertIn("hit@2", result["aggregate"])
        self.assertIn("hit@10", result["aggregate"])
        self.assertIn("recall@2", result["aggregate"])
        self.assertNotIn("hit@5", result["aggregate"])

    def test_baseline_mrr_is_positive(self):
        """fixture와 골든셋이 정합적이면 최소한의 검색 성공이 보장되어야 한다.

        이 단언이 깨지면 fixture/골든셋 자체가 깨졌거나 retrieval에 본격적 회귀가 난 것.
        """
        result = evaluate()
        self.assertGreater(
            result["aggregate"]["mrr"],
            0.0,
            "baseline MRR이 0 — fixture/골든셋 또는 retrieval 회귀 의심",
        )


if __name__ == "__main__":
    unittest.main()
