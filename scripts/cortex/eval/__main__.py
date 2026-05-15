"""CLI entry point: python -m cortex.eval [--golden PATH] [--k N]... [--output PATH]

저장소 동봉 fixture·골든셋으로 retrieval 품질을 측정하고 JSON 결과를 출력한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = evaluate(args.golden, k_values=_resolve_k_values(args))
    _emit_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
