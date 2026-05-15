"""Baseline snapshot serialization and regression diff.

Snapshot은 evaluate() 결과에서 회귀 감지에 필요한 핵심(aggregate + case별 scores)만
추출한 가벼운 dict이다. 검색 결과의 순서(ranked) 같은 노이즈 정보는 제외하여
미세 변경에 안정적이다.

회귀 정의: snapshot의 어떤 metric이라도 baseline 대비 tolerance 이상 떨어지면
회귀로 간주한다. case 추가는 회귀 아님, case 제거는 경고로만 보고한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


SNAPSHOT_VERSION = "v1"


def to_snapshot(eval_result: Mapping) -> dict:
    """evaluate() 결과에서 baseline에 박을 핵심만 추출한다.

    제외 항목: case별 query/expected/ranked (회귀 노이즈 회피).
    """
    return {
        "version": SNAPSHOT_VERSION,
        "k_values": list(eval_result.get("k_values", [])),
        "aggregate": dict(eval_result.get("aggregate", {})),
        "cases": {
            case["id"]: dict(case["scores"])
            for case in eval_result.get("cases", [])
        },
    }


def save_snapshot(eval_result: Mapping, path: str | Path) -> None:
    snapshot = to_snapshot(eval_result)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class MetricChange:
    case_id: str  # 'aggregate' 또는 case id
    metric: str
    baseline: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.baseline

    def __str__(self) -> str:
        return f"{self.case_id}/{self.metric}: {self.baseline:.4f} → {self.current:.4f} (Δ{self.delta:+.4f})"


@dataclass
class DiffReport:
    regressed: list[MetricChange] = field(default_factory=list)
    improved: list[MetricChange] = field(default_factory=list)
    new_cases: list[str] = field(default_factory=list)
    missing_cases: list[str] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return bool(self.regressed)

    def format_text(self) -> str:
        lines: list[str] = ["== Baseline diff =="]
        if not self.regressed and not self.improved and not self.new_cases and not self.missing_cases:
            lines.append("(no changes)")
            return "\n".join(lines)

        if self.regressed:
            lines.append(f"regressed ({len(self.regressed)}):")
            for change in self.regressed:
                lines.append(f"  - {change}")
        if self.improved:
            lines.append(f"improved ({len(self.improved)}):")
            for change in self.improved:
                lines.append(f"  + {change}")
        if self.new_cases:
            lines.append(f"new cases ({len(self.new_cases)}): {', '.join(self.new_cases)}")
        if self.missing_cases:
            lines.append(f"missing cases ({len(self.missing_cases)}): {', '.join(self.missing_cases)}")
        return "\n".join(lines)


def _diff_score_map(
    case_id: str,
    base_scores: Mapping[str, float],
    cur_scores: Mapping[str, float],
    tolerance: float,
    regressed: list[MetricChange],
    improved: list[MetricChange],
) -> None:
    for metric, base_val in base_scores.items():
        if metric not in cur_scores:
            continue
        change = MetricChange(case_id, metric, float(base_val), float(cur_scores[metric]))
        if change.delta < -tolerance:
            regressed.append(change)
        elif change.delta > tolerance:
            improved.append(change)


def compare_snapshots(
    current: Mapping,
    baseline: Mapping,
    tolerance: float = 0.0,
) -> DiffReport:
    """current와 baseline snapshot의 차이를 분석한다."""
    report = DiffReport()

    _diff_score_map(
        "aggregate",
        baseline.get("aggregate", {}),
        current.get("aggregate", {}),
        tolerance,
        report.regressed,
        report.improved,
    )

    cur_cases = current.get("cases", {})
    base_cases = baseline.get("cases", {})

    for case_id, base_scores in base_cases.items():
        if case_id not in cur_cases:
            continue
        _diff_score_map(
            case_id,
            base_scores,
            cur_cases[case_id],
            tolerance,
            report.regressed,
            report.improved,
        )

    report.new_cases = sorted(set(cur_cases) - set(base_cases))
    report.missing_cases = sorted(set(base_cases) - set(cur_cases))
    return report


__all__ = [
    "SNAPSHOT_VERSION",
    "to_snapshot",
    "save_snapshot",
    "load_snapshot",
    "MetricChange",
    "DiffReport",
    "compare_snapshots",
]
