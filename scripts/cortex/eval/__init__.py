"""Retrieval evaluation harness.

저장소에 동봉된 fixture와 골든셋으로 검색 엔진 품질을 정량 측정한다.
사용자 워크스페이스 내용에는 의존하지 않는다(개인화 평가가 아니다).
"""

from cortex.eval.metrics import hit_at_k, mrr, recall_at_k, aggregate_scores
from cortex.eval.golden import GoldenCase, load_golden_set, GoldenSetError

__all__ = [
    "hit_at_k",
    "mrr",
    "recall_at_k",
    "aggregate_scores",
    "GoldenCase",
    "load_golden_set",
    "GoldenSetError",
]
