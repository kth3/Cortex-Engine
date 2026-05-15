"""Golden set loader.

골든셋은 (query, expected_keys) 쌍의 yaml 리스트. 검색 엔진 품질을 정량 측정한다.

필드:
    id (str, 필수): 케이스 식별자. 결과 출력·중복 검출에 사용.
    query (str, 필수): 검색 엔진에 던질 쿼리 문자열.
    expected_keys (list[str], 필수): 검색 결과의 `key`와 비교할 정답 키 집합.
        비어있지 않은 문자열 리스트. top-k 안에 하나라도 있으면 hit.
    domain ("code" | "knowledge" | "observation", 선택): 평가 대상 도메인.
        생략 시 runner가 모든 도메인 결과를 사용.
    notes (str, 선택): 디버깅 메모. 점수 계산에 영향 없음.
    tags (list[str], 선택): 묶음 분석용 태그. 점수 계산에 영향 없음.

예시:
    - id: hybrid-search-module
      query: "hybrid search"
      expected_keys:
        - "scripts/cortex/retrieval/hybrid.py"
      domain: code
      tags: ["retrieval", "core"]

개인화 금지 원칙:
    expected_keys는 저장소 동봉 fixture(scripts/cortex/eval/fixture/ 예정)나
    cortex 저장소의 영구 경로만 참조한다. 사용자 워크스페이스 콘텐츠를 정답으로
    두지 않으며, 모든 사용자가 같은 점수를 재현할 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_DOMAINS = frozenset({"code", "knowledge", "observation"})
REQUIRED_FIELDS = ("id", "query", "expected_keys")


class GoldenSetError(ValueError):
    """골든셋 형식이나 의미가 잘못됐을 때 발생."""


@dataclass(frozen=True)
class GoldenCase:
    """한 평가 케이스. ranked_keys와 expected_keys 비교에 사용된다."""

    id: str
    query: str
    expected_keys: tuple[str, ...]
    domain: str | None = None
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def _require_dict(payload: Any, where: str) -> dict:
    if not isinstance(payload, dict):
        raise GoldenSetError(f"{where}: 각 항목은 매핑이어야 함 (got {type(payload).__name__})")
    return payload


def _require_keys(case: dict, case_index: int) -> None:
    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in case]
    if missing:
        raise GoldenSetError(f"case[{case_index}]: 필수 키 누락 {missing}")


def _validate_domain(domain: Any, case_id: str) -> str | None:
    if domain is None:
        return None
    if not isinstance(domain, str) or domain not in VALID_DOMAINS:
        raise GoldenSetError(
            f"case '{case_id}': domain은 {sorted(VALID_DOMAINS)} 중 하나여야 함 (got {domain!r})"
        )
    return domain


def _validate_expected_keys(expected: Any, case_id: str) -> tuple[str, ...]:
    if not isinstance(expected, list) or not expected:
        raise GoldenSetError(f"case '{case_id}': expected_keys는 비어있지 않은 리스트여야 함")
    out: list[str] = []
    for item in expected:
        if not isinstance(item, str) or not item.strip():
            raise GoldenSetError(f"case '{case_id}': expected_keys 항목은 비어있지 않은 문자열이어야 함")
        out.append(item.strip())
    return tuple(out)


def _validate_tags(tags: Any, case_id: str) -> tuple[str, ...]:
    if tags is None:
        return ()
    if not isinstance(tags, list):
        raise GoldenSetError(f"case '{case_id}': tags는 리스트여야 함")
    for tag in tags:
        if not isinstance(tag, str):
            raise GoldenSetError(f"case '{case_id}': tags 항목은 문자열이어야 함")
    return tuple(tags)


def _parse_case(case_payload: Any, case_index: int) -> GoldenCase:
    case = _require_dict(case_payload, f"case[{case_index}]")
    _require_keys(case, case_index)

    case_id = case["id"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise GoldenSetError(f"case[{case_index}]: id는 비어있지 않은 문자열이어야 함")
    case_id = case_id.strip()

    query = case["query"]
    if not isinstance(query, str) or not query.strip():
        raise GoldenSetError(f"case '{case_id}': query는 비어있지 않은 문자열이어야 함")

    return GoldenCase(
        id=case_id,
        query=query.strip(),
        expected_keys=_validate_expected_keys(case["expected_keys"], case_id),
        domain=_validate_domain(case.get("domain"), case_id),
        notes=str(case.get("notes", "")).strip(),
        tags=_validate_tags(case.get("tags"), case_id),
    )


def _check_unique_ids(cases: list[GoldenCase]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise GoldenSetError(f"중복 id 발견: '{case.id}'")
        seen.add(case.id)


def load_golden_set(path: str | Path) -> list[GoldenCase]:
    """yaml 파일을 읽어 GoldenCase 리스트로 반환."""
    file_path = Path(path)
    if not file_path.exists():
        raise GoldenSetError(f"골든셋 파일을 찾을 수 없음: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)

    if payload is None:
        return []
    if not isinstance(payload, list):
        raise GoldenSetError("골든셋 최상위는 케이스 리스트여야 함")

    cases = [_parse_case(item, index) for index, item in enumerate(payload)]
    _check_unique_ids(cases)
    return cases


__all__ = ["GoldenCase", "load_golden_set", "GoldenSetError", "VALID_DOMAINS"]
