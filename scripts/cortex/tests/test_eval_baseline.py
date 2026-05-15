"""Baseline snapshot regression detection tests.

단위 테스트: to_snapshot, save/load, compare_snapshots 로직.
통합 테스트: 저장소에 박힌 baseline.json이 현재 평가 결과보다 떨어지지 않는지 검증.
이 통합 단언이 깨지면 retrieval에 회귀가 발생한 것.
"""

import json
import tempfile
import unittest
from pathlib import Path

from cortex.eval.baseline import (
    DiffReport,
    SNAPSHOT_VERSION,
    compare_snapshots,
    load_snapshot,
    save_snapshot,
    to_snapshot,
)
from cortex.eval.runner import evaluate

BASELINE_PATH = Path(__file__).resolve().parents[1] / "eval" / "baseline.json"


def _sample_eval_result() -> dict:
    return {
        "total_cases": 2,
        "k_values": [1, 3],
        "aggregate": {"mrr": 0.8, "hit@1": 0.5, "hit@3": 1.0},
        "cases": [
            {
                "id": "case-a",
                "query": "q1",
                "domain": "code",
                "expected": ["fqn-a"],
                "ranked": ["fqn-a"],
                "scores": {"mrr": 1.0, "hit@1": 1.0, "hit@3": 1.0},
            },
            {
                "id": "case-b",
                "query": "q2",
                "domain": "knowledge",
                "expected": ["key-b"],
                "ranked": ["other", "key-b"],
                "scores": {"mrr": 0.5, "hit@1": 0.0, "hit@3": 1.0},
            },
        ],
    }


class ToSnapshotTests(unittest.TestCase):
    def test_extracts_aggregate_and_case_scores_only(self):
        snap = to_snapshot(_sample_eval_result())
        self.assertEqual(snap["version"], SNAPSHOT_VERSION)
        self.assertEqual(snap["k_values"], [1, 3])
        self.assertEqual(snap["aggregate"]["mrr"], 0.8)
        self.assertIn("case-a", snap["cases"])
        self.assertIn("case-b", snap["cases"])
        self.assertEqual(snap["cases"]["case-a"]["mrr"], 1.0)
        # ranked/expected/query는 snapshot에 포함되지 않는다
        self.assertNotIn("ranked", snap["cases"]["case-a"])
        self.assertNotIn("query", snap["cases"]["case-a"])


class SaveLoadSnapshotTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.json"
            save_snapshot(_sample_eval_result(), path)
            loaded = load_snapshot(path)
            self.assertEqual(loaded["aggregate"]["mrr"], 0.8)
            self.assertEqual(set(loaded["cases"].keys()), {"case-a", "case-b"})


class CompareSnapshotsTests(unittest.TestCase):
    def _snap(self, agg: dict, cases: dict) -> dict:
        return {"version": SNAPSHOT_VERSION, "k_values": [1, 3], "aggregate": agg, "cases": cases}

    def test_no_changes_means_no_regression(self):
        snap = self._snap({"mrr": 1.0}, {"a": {"mrr": 1.0}})
        report = compare_snapshots(snap, snap)
        self.assertFalse(report.has_regression)
        self.assertEqual(report.regressed, [])
        self.assertEqual(report.improved, [])
        self.assertEqual(report.new_cases, [])
        self.assertEqual(report.missing_cases, [])

    def test_aggregate_drop_is_regression(self):
        base = self._snap({"mrr": 1.0}, {})
        cur = self._snap({"mrr": 0.5}, {})
        report = compare_snapshots(cur, base)
        self.assertTrue(report.has_regression)
        self.assertEqual(len(report.regressed), 1)
        self.assertEqual(report.regressed[0].case_id, "aggregate")
        self.assertEqual(report.regressed[0].metric, "mrr")

    def test_case_drop_is_regression(self):
        base = self._snap({}, {"a": {"hit@1": 1.0}})
        cur = self._snap({}, {"a": {"hit@1": 0.0}})
        report = compare_snapshots(cur, base)
        self.assertTrue(report.has_regression)
        self.assertEqual(report.regressed[0].case_id, "a")
        self.assertAlmostEqual(report.regressed[0].delta, -1.0, places=6)

    def test_improvement_is_not_regression(self):
        base = self._snap({"mrr": 0.5}, {"a": {"hit@1": 0.0}})
        cur = self._snap({"mrr": 0.9}, {"a": {"hit@1": 1.0}})
        report = compare_snapshots(cur, base)
        self.assertFalse(report.has_regression)
        self.assertEqual(len(report.improved), 2)

    def test_tolerance_absorbs_small_drop(self):
        base = self._snap({"mrr": 1.0}, {})
        cur = self._snap({"mrr": 0.995}, {})
        report = compare_snapshots(cur, base, tolerance=0.01)
        self.assertFalse(report.has_regression)

    def test_new_case_is_not_regression(self):
        base = self._snap({}, {"a": {"hit@1": 1.0}})
        cur = self._snap({}, {"a": {"hit@1": 1.0}, "b": {"hit@1": 1.0}})
        report = compare_snapshots(cur, base)
        self.assertFalse(report.has_regression)
        self.assertEqual(report.new_cases, ["b"])

    def test_missing_case_is_warning_not_regression(self):
        base = self._snap({}, {"a": {"hit@1": 1.0}, "b": {"hit@1": 1.0}})
        cur = self._snap({}, {"a": {"hit@1": 1.0}})
        report = compare_snapshots(cur, base)
        # missing은 regressed로 분류되지 않는다(평가 케이스 축소 시 회귀 아님).
        self.assertFalse(report.has_regression)
        self.assertEqual(report.missing_cases, ["b"])

    def test_format_text_includes_regressed_section(self):
        base = self._snap({"mrr": 1.0}, {"a": {"hit@1": 1.0}})
        cur = self._snap({"mrr": 0.5}, {"a": {"hit@1": 0.0}})
        report = compare_snapshots(cur, base)
        text = report.format_text()
        self.assertIn("regressed", text)
        self.assertIn("aggregate/mrr", text)
        self.assertIn("a/hit@1", text)

    def test_format_text_no_changes(self):
        snap = self._snap({"mrr": 1.0}, {})
        text = compare_snapshots(snap, snap).format_text()
        self.assertIn("no changes", text)


class BaselineRegressionGate(unittest.TestCase):
    """저장소 baseline.json이 현재 평가 결과보다 떨어지지 않아야 한다.

    이 테스트가 실패하면 retrieval에 회귀가 발생한 것. baseline을 갱신하려면
    `python -m cortex.eval --snapshot scripts/cortex/eval/baseline.json` 실행.
    """

    def test_current_evaluation_meets_or_exceeds_baseline(self):
        self.assertTrue(BASELINE_PATH.exists(), f"baseline 누락: {BASELINE_PATH}")
        baseline = load_snapshot(BASELINE_PATH)
        # baseline에 박힌 k_values와 같은 K로 평가
        k_values = tuple(baseline.get("k_values", [1, 3, 5]))
        current = to_snapshot(evaluate(k_values=k_values))
        report = compare_snapshots(current, baseline)
        if report.has_regression:
            self.fail("Retrieval 회귀 감지:\n" + report.format_text())


if __name__ == "__main__":
    unittest.main()
