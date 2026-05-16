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
from cortex.paths import data_home, resolve_workspace, workspace_data_dir
from cortex.runtime import knowledge_cli

HF_TOKEN_ENV_KEY = "HF_TOKEN"


def _upsert_env(path: Path, key: str, value: str) -> None:
    """Insert or update `key=value` in a dotenv-style file, preserving other lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    found = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _save_hf_token(token: str) -> dict:
    env_path = data_home() / ".env"
    _upsert_env(env_path, HF_TOKEN_ENV_KEY, token)
    return {"status": "saved", "path": str(env_path)}


def _warm_models(token: str | None, model_id: str | None, dry_run: bool) -> dict:
    if dry_run:
        return {"status": "dry-run-skip"}
    try:
        from cortex.embeddings.provider import MODEL_ID as default_model_id
        from huggingface_hub import snapshot_download
    except Exception as exc:
        return {"status": "import-error", "error": str(exc)}
    target_model = (model_id or "").strip() or default_model_id
    try:
        snapshot_download(
            repo_id=target_model,
            token=token or os.environ.get(HF_TOKEN_ENV_KEY) or None,
            resume_download=True,
            max_workers=4,
        )
    except Exception as exc:
        return {"status": "error", "model": target_model, "error": str(exc)}
    return {"status": "ok", "model": target_model}


def _save_embedding_config(model_id: str | None, max_seq_length: int | None) -> dict:
    env_path = data_home() / ".env"
    saved: dict = {}
    if model_id:
        _upsert_env(env_path, "CORTEX_EMBEDDING_MODEL", model_id)
        saved["model"] = model_id
    if max_seq_length is not None:
        _upsert_env(env_path, "CORTEX_EMBEDDING_MAX_SEQ_LENGTH", str(max_seq_length))
        saved["max_seq_length"] = max_seq_length
    payload: dict = {
        "status": "saved",
        "path": str(env_path),
        "saved": saved,
    }
    if model_id:
        payload["warning"] = (
            "Embedding model changed. Existing vectors may be incompatible. "
            "Run 'cortex-index --force' to rebuild the index if dimensions differ."
        )
    return payload


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
