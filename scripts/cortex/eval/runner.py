"""End-to-end evaluation runner.

흐름:
    1. tempfile.mkdtemp로 임시 디렉토리를 만들고 CORTEX_HOME 환경변수를 그곳으로 격리.
    2. fixture/loader.setup_fixture_db로 nodes·memories를 적재.
    3. 골든셋 yaml을 로드.
    4. 각 케이스에 대해 unified_pipeline_search(ve_module=None)을 호출하고
       도메인 필터링 후 metrics(MRR, hit@k, recall@k)를 계산.
    5. case별·전체 aggregate 점수를 dict로 반환.

벡터 검색은 v1 범위 밖이며 ve_module=None으로 FTS·observation 경로만 평가한다.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from cortex.eval.fixture.loader import setup_fixture_db
from cortex.eval.golden import GoldenCase, load_golden_set
from cortex.eval.metrics import aggregate_scores, hit_at_k, mrr, recall_at_k

DEFAULT_GOLDEN_PATH = Path(__file__).parent / "golden" / "queries.yaml"
DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5)


@contextlib.contextmanager
def fixture_workspace():
    """임시 CORTEX_HOME을 설정하고 fixture를 적재한 워크스페이스 경로를 yield."""
    tmpdir = tempfile.mkdtemp(prefix="cortex_eval_")
    previous_home = os.environ.get("CORTEX_HOME")
    os.environ["CORTEX_HOME"] = tmpdir
    try:
        setup_fixture_db(tmpdir)
        yield tmpdir
    finally:
        if previous_home is None:
            os.environ.pop("CORTEX_HOME", None)
        else:
            os.environ["CORTEX_HOME"] = previous_home
        shutil.rmtree(tmpdir, ignore_errors=True)


def _ranked_keys(case: GoldenCase, results: list[dict]) -> list[str]:
    if case.domain:
        return [r.get("key", "") for r in results if r.get("domain") == case.domain]
    return [r.get("key", "") for r in results]


def _case_scores(ranked: list[str], expected: set[str], k_values: tuple[int, ...]) -> dict[str, float]:
    scores: dict[str, float] = {"mrr": mrr(ranked, expected)}
    for k in k_values:
        scores[f"hit@{k}"] = 1.0 if hit_at_k(ranked, expected, k) else 0.0
        scores[f"recall@{k}"] = recall_at_k(ranked, expected, k)
    return scores


def _evaluate_case(case: GoldenCase, workspace: str, k_values: tuple[int, ...]) -> dict:
    from cortex.retrieval.hybrid import unified_pipeline_search

    max_k = max(k_values)
    results = unified_pipeline_search(workspace, case.query, limit=max_k, ve_module=None)
    ranked = _ranked_keys(case, results)
    expected = set(case.expected_keys)

    return {
        "id": case.id,
        "query": case.query,
        "domain": case.domain,
        "expected": list(case.expected_keys),
        "ranked": ranked[:max_k],
        "scores": _case_scores(ranked, expected, k_values),
    }


def _aggregate_metric_names(k_values: tuple[int, ...]) -> list[str]:
    names = ["mrr"]
    names.extend(f"hit@{k}" for k in k_values)
    names.extend(f"recall@{k}" for k in k_values)
    return names


def evaluate(
    golden_path: str | Path = DEFAULT_GOLDEN_PATH,
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
) -> dict:
    """골든셋 전체를 평가하고 case별·aggregate 점수를 반환한다."""
    cases = load_golden_set(golden_path)
    case_results: list[dict] = []
    with fixture_workspace() as workspace:
        for case in cases:
            case_results.append(_evaluate_case(case, workspace, k_values))

    aggregate = aggregate_scores(
        [c["scores"] for c in case_results],
        metric_names=_aggregate_metric_names(k_values),
    )

    return {
        "total_cases": len(case_results),
        "k_values": list(k_values),
        "aggregate": aggregate,
        "cases": case_results,
    }


__all__ = [
    "DEFAULT_GOLDEN_PATH",
    "DEFAULT_K_VALUES",
    "evaluate",
    "fixture_workspace",
]
