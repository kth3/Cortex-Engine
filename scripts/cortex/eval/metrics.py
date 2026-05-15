"""Information retrieval metrics.

순수 함수만 모은다. 검색 엔진·골든셋 형식에 종속되지 않으며,
ranked_keys(검색 결과 키 순서)와 expected_keys(정답 키 집합)만 받는다.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def hit_at_k(ranked_keys: list[str], expected_keys: Iterable[str], k: int) -> bool:
    """top-k 결과 안에 expected 중 하나라도 있으면 True.

    expected가 비어 있으면 False (정답 없음 → hit 정의 불가).
    """
    expected_set = set(expected_keys)
    if not expected_set or k <= 0:
        return False
    for key in ranked_keys[:k]:
        if key in expected_set:
            return True
    return False


def mrr(ranked_keys: list[str], expected_keys: Iterable[str]) -> float:
    """Reciprocal rank: expected 중 가장 빨리 등장하는 키의 1/rank. 없으면 0.0.

    rank는 1-based.
    """
    expected_set = set(expected_keys)
    if not expected_set:
        return 0.0
    for index, key in enumerate(ranked_keys, start=1):
        if key in expected_set:
            return 1.0 / index
    return 0.0


def recall_at_k(ranked_keys: list[str], expected_keys: Iterable[str], k: int) -> float:
    """top-k 안에 들어온 expected 비율. expected가 비어 있으면 0.0."""
    expected_set = set(expected_keys)
    if not expected_set or k <= 0:
        return 0.0
    found = sum(1 for key in ranked_keys[:k] if key in expected_set)
    return found / len(expected_set)


def aggregate_scores(
    case_scores: list[Mapping[str, float]],
    metric_names: Iterable[str] | None = None,
) -> dict[str, float]:
    """케이스별 점수 dict 리스트를 받아 metric별 평균을 반환.

    누락된 metric은 0.0으로 취급하지 않고 평균 모집단에서 제외한다.
    빈 입력은 모든 metric 0.0.
    """
    if not case_scores:
        return {name: 0.0 for name in (metric_names or [])}

    if metric_names is None:
        seen: set[str] = set()
        for score in case_scores:
            seen.update(score.keys())
        metric_names = sorted(seen)

    averaged: dict[str, float] = {}
    for name in metric_names:
        values = [score[name] for score in case_scores if name in score]
        averaged[name] = sum(values) / len(values) if values else 0.0
    return averaged


__all__ = ["hit_at_k", "mrr", "recall_at_k", "aggregate_scores"]
