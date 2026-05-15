"""CLI entry point: python -m cortex.eval [--golden PATH] [--k N]... [--output PATH]
                                          [--snapshot PATH | --compare PATH] [--tolerance F]

저장소 동봉 fixture·골든셋으로 retrieval 품질을 측정한다.

모드:
    기본              결과 JSON을 --output 또는 stdout으로 출력.
    --snapshot PATH   baseline.json 형식(aggregate + case별 scores)으로 저장.
    --compare PATH    저장된 baseline과 비교, 회귀가 있으면 exit code 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cortex.eval.baseline import (
    compare_snapshots,
    load_snapshot,
    save_snapshot,
    to_snapshot,
)
from cortex.eval.runner import DEFAULT_GOLDEN_PATH, DEFAULT_K_VALUES, evaluate


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex.eval",
        description="Cortex retrieval evaluation harness (저장소 동봉 fixture 기반)",
    )
    parser.add_argument(
        "--golden",
        default=str(DEFAULT_GOLDEN_PATH),
        help=f"골든셋 yaml 경로 (기본: {DEFAULT_GOLDEN_PATH})",
    )
    parser.add_argument(
        "--k",
        type=int,
        action="append",
        default=None,
        help="hit/recall의 K 값 (반복 지정 가능, 기본 1 3 5)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="결과 JSON 파일 경로 (생략 시 stdout)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--snapshot",
        default=None,
        help="baseline snapshot 경로에 결과를 저장한다 (--output과 별개)",
    )
    mode.add_argument(
        "--compare",
        default=None,
        help="저장된 baseline snapshot과 비교, 회귀가 있으면 exit 1",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="--compare 시 허용 오차 (기본 0.0 = 어떤 하락도 회귀)",
    )
    return parser


def _resolve_k_values(args) -> tuple[int, ...]:
    if args.k:
        return tuple(args.k)
    return DEFAULT_K_VALUES


def _emit_result(result: dict, output: str | None) -> None:
    serialized = json.dumps(result, indent=2, ensure_ascii=False)
    if output:
        Path(output).write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized + "\n")


def _run_compare(result: dict, baseline_path: str, tolerance: float) -> int:
    baseline = load_snapshot(baseline_path)
    current = to_snapshot(result)
    report = compare_snapshots(current, baseline, tolerance=tolerance)
    sys.stdout.write(report.format_text() + "\n")
    return 1 if report.has_regression else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = evaluate(args.golden, k_values=_resolve_k_values(args))

    if args.compare is not None:
        return _run_compare(result, args.compare, args.tolerance)

    if args.snapshot is not None:
        save_snapshot(result, args.snapshot)
        if args.output:
            _emit_result(result, args.output)
        return 0

    _emit_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
