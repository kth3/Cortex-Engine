"""Shared helpers for the Cortex bootstrap CLI."""
from __future__ import annotations

import argparse
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from cortex.paths import data_home
from cortex.runtime import knowledge_cli

HF_TOKEN_ENV_KEY = "HF_TOKEN"


def upsert_env(path: Path, key: str, value: str) -> None:
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


def save_hf_token(token: str) -> dict:
    env_path = data_home() / ".env"
    upsert_env(env_path, HF_TOKEN_ENV_KEY, token)
    return {"status": "saved", "path": str(env_path)}


def warm_models(token: str | None, model_id: str | None, dry_run: bool) -> dict:
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


def save_embedding_config(model_id: str | None, max_seq_length: int | None) -> dict:
    env_path = data_home() / ".env"
    saved: dict = {}
    if model_id:
        upsert_env(env_path, "CORTEX_EMBEDDING_MODEL", model_id)
        saved["model"] = model_id
    if max_seq_length is not None:
        upsert_env(env_path, "CORTEX_EMBEDDING_MAX_SEQ_LENGTH", str(max_seq_length))
        saved["max_seq_length"] = max_seq_length
    payload: dict = {
        "status": "saved",
        "path": str(env_path),
        "saved": saved,
    }
    if model_id:
        payload["warning"] = (
            "Embedding model changed. Existing vectors may be incompatible. "
            "Run 'cortex-watcher index --workspace <workspace> --force' to rebuild the index if dimensions differ."
        )
    return payload


def hook_install_namespace(
    *,
    hook_home_key: str,
    include_all: bool,
    timeout: int,
    dry_run: bool,
    hook_command: str | None,
    cortex_home: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        **{hook_home_key: None},
        cortex_home=str(cortex_home) if cortex_home is not None else None,
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


def expand_knowledge(workspace: Path, force: bool, dry_run: bool) -> dict:
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
