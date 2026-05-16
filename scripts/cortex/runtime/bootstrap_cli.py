"""cortex-ctl bootstrap — install Codex + Claude Code hooks and initialize global data dir."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from cortex.integrations import claude_hook, codex_hook
from cortex.paths import resolve_workspace, workspace_data_dir
from cortex.runtime import knowledge_cli


def _hook_install_namespace(
    *,
    hook_home_key: str,
    include_all: bool,
    timeout: int,
    dry_run: bool,
    hook_command: str | None,
) -> argparse.Namespace:
    return argparse.Namespace(
        **{hook_home_key: None},
        profile="safe",
        include_user_prompt_submit=include_all,
        include_stop=include_all,
        include_pre_tool_use=include_all,
        include_post_tool_use=include_all,
        include_all=include_all,
        hook_command=hook_command,
        timeout=timeout,
        dry_run=dry_run,
    )


def _expand_knowledge(workspace: Path, force: bool, dry_run: bool) -> dict:
    if dry_run:
        return {"action": "enable", "status": "dry-run-skip"}
    argv = ["enable"]
    if force:
        argv.append("--force")
    saved = os.environ.get("CORTEX_WORKSPACE")
    os.environ["CORTEX_WORKSPACE"] = str(workspace)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = knowledge_cli.main(argv)
    finally:
        if saved is None:
            os.environ.pop("CORTEX_WORKSPACE", None)
        else:
            os.environ["CORTEX_WORKSPACE"] = saved
    raw = buf.getvalue().strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw}
    payload["exit_code"] = exit_code
    return payload


def _run_bootstrap(args: argparse.Namespace) -> int:
    workspace = resolve_workspace()
    result: dict = {
        "action": "bootstrap",
        "workspace": str(workspace),
        "dryRun": bool(args.dry_run),
    }

    if not args.dry_run:
        result["workspace_data_dir"] = str(workspace_data_dir(workspace))
    else:
        result["workspace_data_dir"] = str(workspace_data_dir(workspace))

    if not args.skip_codex:
        codex_args = _hook_install_namespace(
            hook_home_key="codex_home",
            include_all=args.include_all,
            timeout=codex_hook.DEFAULT_HOOK_TIMEOUT_SECONDS,
            dry_run=args.dry_run,
            hook_command=args.codex_hook_command,
        )
        result["codex"] = codex_hook.install_hooks(codex_args)

    if not args.skip_claude:
        claude_args = _hook_install_namespace(
            hook_home_key="claude_home",
            include_all=args.include_all,
            timeout=claude_hook.DEFAULT_HOOK_TIMEOUT_SECONDS,
            dry_run=args.dry_run,
            hook_command=args.claude_hook_command,
        )
        result["claude"] = claude_hook.install_hooks(claude_args)

    if args.enable_knowledge:
        result["knowledge"] = _expand_knowledge(
            workspace=workspace,
            force=args.force_knowledge,
            dry_run=args.dry_run,
        )

    print(json.dumps(result, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-ctl bootstrap",
        description="Install Cortex hooks for Codex and Claude Code and initialize global data dir.",
    )
    parser.add_argument("--skip-codex", action="store_true", help="Do not install Codex hooks.")
    parser.add_argument("--skip-claude", action="store_true", help="Do not install Claude Code hooks.")
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Install every supported hook event for both adapters (default: SessionStart only).",
    )
    parser.add_argument("--enable-knowledge", action="store_true", help="Also expand knowledge.zip.")
    parser.add_argument("--force-knowledge", action="store_true", help="Overwrite existing knowledge expansion.")
    parser.add_argument("--codex-hook-command", default=None, help="Override cortex-codex-hook path.")
    parser.add_argument("--claude-hook-command", default=None, help="Override cortex-claude-hook path.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only — do not write files.")
    parser.set_defaults(handler=_run_bootstrap)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)
