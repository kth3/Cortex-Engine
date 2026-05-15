"""Retrieval evaluation harness.

저장소에 동봉된 fixture와 골든셋으로 검색 엔진 품질을 정량 측정한다.
사용자 워크스페이스 내용에는 의존하지 않는다(개인화 평가가 아니다).
"""

from cortex.eval.golden import GoldenCase, GoldenSetError, load_golden_set
from cortex.eval.metrics import aggregate_scores, hit_at_k, mrr, recall_at_k
from cortex.eval.runner import (
    DEFAULT_GOLDEN_PATH,
    DEFAULT_K_VALUES,
    evaluate,
    fixture_workspace,
)

__all__ = [
    "hit_at_k",
    "mrr",
    "recall_at_k",
    "aggregate_scores",
    "GoldenCase",
    "load_golden_set",
    "GoldenSetError",
    "evaluate",
    "fixture_workspace",
    "DEFAULT_GOLDEN_PATH",
    "DEFAULT_K_VALUES",
]
