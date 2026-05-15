"""Backward-compatible SessionStart entrypoint for Cortex Codex hooks."""
from __future__ import annotations

import argparse
import json
import sys

from cortex.integrations.codex_hook import (
    DEFAULT_TOKEN_BUDGET,
    EVENT_SESSION_START,
    _empty_output,
    _read_stdin_json,
    run_event,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Cortex Codex SessionStart hook adapter.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--cortex-home", default=None)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--quiet-empty", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload, _raw = _read_stdin_json()
    try:
        print(
            json.dumps(
                run_event(
                    EVENT_SESSION_START,
                    payload,
                    raw_workspace=args.workspace,
                    raw_cortex_home=args.cortex_home,
                    token_budget=args.token_budget,
                ),
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        print(f"[Cortex Codex SessionStart unavailable: {exc}]", file=sys.stderr)
        print(_empty_output())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
