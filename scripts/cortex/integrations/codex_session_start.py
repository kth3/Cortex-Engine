"""Codex SessionStart hook adapter for Cortex auto context."""
from __future__ import annotations

import argparse
import os
import sys

from cortex.mcp.context import McpContext
from cortex.mcp.tools.session import call_pc_auto_context
from cortex.paths import resolve_workspace

DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_SESSION_ID = "codex-session-start"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit Cortex auto context for Codex SessionStart hooks.")
    parser.add_argument("--workspace", default=None, help="Workspace path. Defaults to CORTEX_WORKSPACE or cwd.")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--quiet-empty", action="store_true", help="Do not print anything when no context is found.")
    return parser


def _workspace(raw_workspace: str | None) -> str:
    candidate = raw_workspace or os.environ.get("CORTEX_WORKSPACE") or os.getcwd()
    return str(resolve_workspace(candidate))


def _session_id() -> str:
    return os.environ.get("CODEX_SESSION_ID") or os.environ.get("CORTEX_SESSION_ID") or DEFAULT_SESSION_ID


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workspace = _workspace(args.workspace)
    ctx = McpContext(workspace=workspace, session_id=_session_id(), scripts_dir=None)

    try:
        result = call_pc_auto_context(ctx, {"token_budget": args.token_budget})
    except Exception as exc:
        print(f"[Cortex auto context unavailable: {exc}]", file=sys.stderr)
        return 0

    context = result.get("context", "")
    if not context:
        if not args.quiet_empty:
            print("Cortex auto context: (empty)")
        return 0

    print("Cortex auto context:")
    print(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
