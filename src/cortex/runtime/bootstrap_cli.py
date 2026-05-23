"""cortex-ctl bootstrap — install Codex hooks and initialize global data dir."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cortex.integrations import claude_hook, codex_hook
from cortex.paths import resolve_workspace, workspace_data_dir
from cortex.runtime.bootstrap_support import (
    expand_knowledge as _expand_knowledge,
    hook_install_namespace as _hook_install_namespace,
    save_embedding_config as _save_embedding_config,
    save_hf_token as _save_hf_token,
    warm_models as _warm_models,
)


def _run_bootstrap(args: argparse.Namespace) -> int:
    workspace = resolve_workspace()
    cortex_home = Path(__file__).resolve().parents[3]
    result: dict = {
        "action": "bootstrap",
        "workspace": str(workspace),
        "cortexHome": str(cortex_home),
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
            cortex_home=cortex_home,
        )
        result["codex"] = codex_hook.install_hooks(codex_args)

    if args.include_claude:
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

    if args.hf_token:
        if args.dry_run:
            result["hf_token"] = {"status": "dry-run-skip"}
        else:
            result["hf_token"] = _save_hf_token(args.hf_token)

    if args.embedding_model or args.embedding_max_seq_length is not None:
        if args.dry_run:
            result["embedding"] = {"status": "dry-run-skip"}
        else:
            result["embedding"] = _save_embedding_config(
                args.embedding_model,
                args.embedding_max_seq_length,
            )

    if args.warm_models:
        result["warm_models"] = _warm_models(
            token=args.hf_token,
            model_id=args.embedding_model,
            dry_run=args.dry_run,
        )

    print(json.dumps(result, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-ctl bootstrap",
        description="Install Cortex hooks for Codex and initialize global data dir.",
    )
    parser.add_argument("--skip-codex", action="store_true", help="Do not install Codex hooks.")
    parser.add_argument("--include-claude", action="store_true", help="Also install Claude Code hooks.")
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Install every supported hook event for selected adapters (default: SessionStart only).",
    )
    parser.add_argument("--enable-knowledge", action="store_true", help="Also expand knowledge.zip.")
    parser.add_argument("--force-knowledge", action="store_true", help="Overwrite existing knowledge expansion.")
    parser.add_argument("--codex-hook-command", default=None, help="Override cortex-codex-hook path.")
    parser.add_argument("--claude-hook-command", default=None, help="Override cortex-claude-hook path when --include-claude is set.")
    parser.add_argument(
        "--hf-token",
        default=None,
        help="HuggingFace access token. Saved to <CORTEX_DATA_HOME>/.env for future runs.",
    )
    parser.add_argument(
        "--warm-models",
        action="store_true",
        help="Pre-download the embedding model so the first MCP call doesn't pay the cost.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Override embedding model (e.g. google/embeddinggemma-300m). Saved to <CORTEX_DATA_HOME>/.env.",
    )
    parser.add_argument(
        "--embedding-max-seq-length",
        type=int,
        default=None,
        help="Override model context window. Saved to <CORTEX_DATA_HOME>/.env.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only — do not write files.")
    parser.set_defaults(handler=_run_bootstrap)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)
