"""Global Codex hook installer and runtime adapter for Cortex."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cortex.mcp.context import McpContext
from cortex.mcp.tools.memory import call_save_observation
from cortex.mcp.tools.search import call_pc_capsule, call_pc_skeleton
from cortex.mcp.tools.session import call_pc_auto_context, call_pc_session_sync

HOOKS_JSON_FILENAME = "hooks.json"
HOOK_ENTRY_NAME = "cortex-codex-hook"
DEFAULT_PROFILE = "safe"
DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_SESSION_ID = "codex-hook"
DEFAULT_HOOK_TIMEOUT_SECONDS = 45
MAX_STOP_TASK_DESC_CHARS = 2000

EVENT_SESSION_START = "SessionStart"
EVENT_USER_PROMPT_SUBMIT = "UserPromptSubmit"
EVENT_STOP = "Stop"
EVENT_PRE_TOOL_USE = "PreToolUse"
EVENT_POST_TOOL_USE = "PostToolUse"
SUPPORTED_RUN_EVENTS = (
    EVENT_SESSION_START,
    EVENT_USER_PROMPT_SUBMIT,
    EVENT_STOP,
    EVENT_PRE_TOOL_USE,
    EVENT_POST_TOOL_USE,
)

APPLY_PATCH_MATCHER = "apply_patch"

HOOK_MARKER = HOOK_ENTRY_NAME


def _json_output(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _empty_output() -> str:
    return "{}"


def _emit_empty() -> None:
    print(_empty_output())


def _codex_home(raw_codex_home: str | None = None) -> Path:
    if raw_codex_home:
        return Path(raw_codex_home).expanduser().resolve()
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def _hooks_json_path(codex_home: Path) -> Path:
    return codex_home / HOOKS_JSON_FILENAME


def _default_hook_command_path() -> Path:
    """Resolve the cortex-codex-hook entry point.

    1. uv tool-installed shim on PATH (global install).
    2. Current python's venv (development fallback).
    """
    found = shutil.which(HOOK_ENTRY_NAME)
    if found:
        return Path(found)
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / HOOK_ENTRY_NAME
    if os.name == "nt":
        candidate = candidate.with_suffix(".exe")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"'{HOOK_ENTRY_NAME}' not found in PATH or current venv. "
        "Run 'uv tool install cortex-agent' or provide --hook-command."
    )


def _read_stdin_json() -> tuple[dict[str, Any], str]:
    raw = sys.stdin.read()
    normalized = raw.lstrip("\ufeff")
    if not normalized.strip():
        return {}, raw
    try:
        data = json.loads(normalized)
    except Exception as exc:
        print(f"[Cortex Codex hook ignored invalid JSON: {exc}]", file=sys.stderr)
        return {}, raw
    return data if isinstance(data, dict) else {}, raw


def _find_workspace(start_path: str | os.PathLike[str] | None) -> Path:
    curr = Path(start_path or os.getcwd()).expanduser().resolve()
    for parent in (curr, *curr.parents):
        if (parent / ".git").exists():
            return parent
    return curr


def _find_cortex_home_from_workspace(workspace: Path) -> Path | None:
    for base in (workspace, *workspace.parents):
        if base.name == ".cortex" and (base / "pyproject.toml").exists():
            return base
        candidate = base / ".cortex"
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return None


def _resolve_workspace(payload: dict[str, Any], raw_workspace: str | None) -> Path:
    if raw_workspace:
        return _find_workspace(raw_workspace)
    return _find_workspace(
        payload.get("cwd") or os.environ.get("CORTEX_WORKSPACE") or os.getcwd()
    )


def _resolve_cortex_home(
    payload: dict[str, Any],
    workspace: Path,
    raw_cortex_home: str | None,
) -> Path | None:
    if raw_cortex_home:
        return Path(raw_cortex_home).expanduser().resolve()

    payload_cwd = payload.get("cwd")
    if payload_cwd:
        from_payload = _find_cortex_home_from_workspace(Path(payload_cwd))
        if from_payload is not None:
            return from_payload

    env_home = os.environ.get("CORTEX_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    return _find_cortex_home_from_workspace(workspace)


def _session_id(payload: dict[str, Any]) -> str:
    return (
        str(payload.get("session_id") or "")
        or os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CORTEX_SESSION_ID")
        or DEFAULT_SESSION_ID
    )


def _context_from_payload(
    payload: dict[str, Any],
    raw_workspace: str | None,
    raw_cortex_home: str | None,
) -> McpContext | None:
    workspace = _resolve_workspace(payload, raw_workspace)
    cortex_home = _resolve_cortex_home(payload, workspace, raw_cortex_home)
    if cortex_home is None:
        print(
            f"[Cortex Codex hook skipped: .cortex not found for {workspace}]",
            file=sys.stderr,
        )
        return None
    return McpContext(
        workspace=str(workspace),
        session_id=_session_id(payload),
        scripts_dir=cortex_home / "scripts",
    )


def _hook_specific_output(event_name: str, additional_context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        }
    }


def _run_session_start(ctx: McpContext, token_budget: int) -> dict[str, Any]:
    result = call_pc_auto_context(ctx, {"token_budget": token_budget})
    context = str(result.get("context") or "").strip()
    if not context:
        return {}
    return _hook_specific_output(
        EVENT_SESSION_START,
        f"Cortex auto context:\n{context}",
    )


def _run_user_prompt_submit(
    ctx: McpContext,
    payload: dict[str, Any],
    token_budget: int,
) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return {}

    result = call_pc_capsule(
        ctx,
        {
            "query": prompt,
            "token_budget": token_budget,
            "auto_chain": False,
        },
    )
    capsule = str(result.get("capsule") or "").strip()
    if not capsule:
        return {}
    return _hook_specific_output(
        EVENT_USER_PROMPT_SUBMIT,
        f"Cortex prompt context:\n{capsule}",
    )


def _run_stop(ctx: McpContext, payload: dict[str, Any]) -> dict[str, Any]:
    task_desc = str(payload.get("last_assistant_message") or "").strip()
    if not task_desc:
        return {}
    if len(task_desc) > MAX_STOP_TASK_DESC_CHARS:
        task_desc = task_desc[:MAX_STOP_TASK_DESC_CHARS]
    try:
        call_pc_session_sync(ctx, {"task_desc": task_desc})
    except Exception as exc:
        print(f"[Cortex session_sync skipped: {exc}]", file=sys.stderr)
    return {}


def _tool_input_file_path(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("path", "file_path", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _run_pre_tool_use(ctx: McpContext, payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("tool_name") or "") != APPLY_PATCH_MATCHER:
        return {}
    file_path = _tool_input_file_path(payload.get("tool_input"))
    if not file_path:
        return {}
    try:
        skeleton = call_pc_skeleton(ctx, {"file_path": file_path, "detail": "standard"})
    except Exception as exc:
        print(f"[Cortex skeleton skipped: {exc}]", file=sys.stderr)
        return {}
    skeleton_text = str(skeleton or "").strip()
    if not skeleton_text:
        return {}
    return _hook_specific_output(
        EVENT_PRE_TOOL_USE,
        f"Cortex skeleton for {file_path}:\n{skeleton_text}",
    )


def _run_post_tool_use(ctx: McpContext, payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    if tool_name != APPLY_PATCH_MATCHER:
        return {}
    file_path = _tool_input_file_path(payload.get("tool_input"))
    if not file_path:
        return {}
    content = f"apply_patch edited {file_path}"
    turn_id = payload.get("turn_id")
    if turn_id:
        content += f" (turn={turn_id})"
    try:
        call_save_observation(ctx, {"content": content, "file_paths": [file_path]})
    except Exception as exc:
        print(f"[Cortex save_observation skipped: {exc}]", file=sys.stderr)
    return {}


def run_event(
    event_name: str,
    payload: dict[str, Any],
    raw_workspace: str | None = None,
    raw_cortex_home: str | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict[str, Any]:
    ctx = _context_from_payload(payload, raw_workspace, raw_cortex_home)
    if ctx is None:
        return {}

    if event_name == EVENT_SESSION_START:
        return _run_session_start(ctx, token_budget)
    if event_name == EVENT_USER_PROMPT_SUBMIT:
        return _run_user_prompt_submit(ctx, payload, token_budget)
    if event_name == EVENT_STOP:
        return _run_stop(ctx, payload)
    if event_name == EVENT_PRE_TOOL_USE:
        return _run_pre_tool_use(ctx, payload)
    if event_name == EVENT_POST_TOOL_USE:
        return _run_post_tool_use(ctx, payload)
    print(f"[Cortex Codex hook skipped unsupported event: {event_name}]", file=sys.stderr)
    return {}


def _hook_command(hook_command_path: Path, event_name: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([str(hook_command_path), "run", event_name])
    return " ".join(
        [
            shlex.quote(str(hook_command_path)),
            "run",
            shlex.quote(event_name),
        ]
    )


def _load_hooks_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        data["hooks"] = {}
    return data


def _is_cortex_hook(handler: dict[str, Any], event_name: str) -> bool:
    command = str(handler.get("command") or "")
    return HOOK_MARKER in command and event_name in command


_EVENT_STATUS_MESSAGES = {
    EVENT_SESSION_START: "Loading Cortex context",
    EVENT_USER_PROMPT_SUBMIT: "Searching Cortex context",
    EVENT_STOP: "Syncing Cortex session",
    EVENT_PRE_TOOL_USE: "Loading Cortex skeleton",
    EVENT_POST_TOOL_USE: "Recording Cortex observation",
}


def _event_status_message(event_name: str) -> str:
    return _EVENT_STATUS_MESSAGES.get(event_name, "Cortex hook")


def _install_event_hook(
    data: dict[str, Any],
    event_name: str,
    command: str,
    timeout: int,
    matcher: str | None = None,
) -> None:
    event_groups = data.setdefault("hooks", {}).setdefault(event_name, [])
    for group in event_groups:
        handlers = group.get("hooks", [])
        for handler in handlers:
            if isinstance(handler, dict) and _is_cortex_hook(handler, event_name):
                handler.update(
                    {
                        "type": "command",
                        "command": command,
                        "timeout": timeout,
                        "statusMessage": _event_status_message(event_name),
                    }
                )
                if matcher is not None:
                    group["matcher"] = matcher
                return

    group: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout,
                "statusMessage": _event_status_message(event_name),
            }
        ]
    }
    if matcher is not None:
        group["matcher"] = matcher
    event_groups.append(group)


def _install_events(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    include_all = bool(getattr(args, "include_all", False))
    pairs: list[tuple[str, str | None]] = [(EVENT_SESSION_START, None)]
    if include_all or args.include_user_prompt_submit:
        pairs.append((EVENT_USER_PROMPT_SUBMIT, None))
    if include_all or getattr(args, "include_stop", False):
        pairs.append((EVENT_STOP, None))
    if include_all or getattr(args, "include_pre_tool_use", False):
        pairs.append((EVENT_PRE_TOOL_USE, APPLY_PATCH_MATCHER))
    if include_all or getattr(args, "include_post_tool_use", False):
        pairs.append((EVENT_POST_TOOL_USE, APPLY_PATCH_MATCHER))
    return pairs


def install_hooks(args: argparse.Namespace) -> dict[str, Any]:
    codex_home = _codex_home(args.codex_home)
    hooks_json = _hooks_json_path(codex_home)
    event_pairs = _install_events(args)

    hook_cmd = (
        Path(args.hook_command).expanduser()
        if getattr(args, "hook_command", None)
        else _default_hook_command_path()
    )

    data = _load_hooks_json(hooks_json)
    for event_name, matcher in event_pairs:
        _install_event_hook(
            data,
            event_name,
            _hook_command(hook_cmd, event_name),
            args.timeout,
            matcher=matcher,
        )

    result = {
        "codexHome": str(codex_home),
        "hookCommand": str(hook_cmd),
        "hooksJson": str(hooks_json),
        "events": [event_name for event_name, _ in event_pairs],
        "hooks": data,
        "dryRun": bool(args.dry_run),
    }

    if args.dry_run:
        return result

    hooks_json.parent.mkdir(parents=True, exist_ok=True)
    hooks_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and run Cortex Codex hooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a Cortex adapter for one Codex hook event.")
    run_parser.add_argument("event", choices=SUPPORTED_RUN_EVENTS)
    run_parser.add_argument("--workspace", default=None)
    run_parser.add_argument("--cortex-home", default=None)
    run_parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)

    install_parser = subparsers.add_parser("install", help="Register cortex-codex-hook entries in Codex hooks.json.")
    install_parser.add_argument("--codex-home", default=None)
    install_parser.add_argument("--profile", choices=(DEFAULT_PROFILE,), default=DEFAULT_PROFILE)
    install_parser.add_argument("--include-user-prompt-submit", action="store_true")
    install_parser.add_argument("--include-stop", action="store_true")
    install_parser.add_argument("--include-pre-tool-use", action="store_true")
    install_parser.add_argument("--include-post-tool-use", action="store_true")
    install_parser.add_argument(
        "--include-all",
        action="store_true",
        help="Install every supported event in addition to SessionStart.",
    )
    install_parser.add_argument(
        "--hook-command",
        default=None,
        help="Absolute path to cortex-codex-hook (defaults to PATH lookup, then current venv).",
    )
    install_parser.add_argument("--timeout", type=int, default=DEFAULT_HOOK_TIMEOUT_SECONDS)
    install_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "install":
        print(_json_output(install_hooks(args)))
        return 0

    payload, _raw = _read_stdin_json()
    try:
        print(
            _json_output(
                run_event(
                    args.event,
                    payload,
                    raw_workspace=args.workspace,
                    raw_cortex_home=args.cortex_home,
                    token_budget=args.token_budget,
                )
            )
        )
    except Exception as exc:
        print(f"[Cortex Codex hook unavailable: {exc}]", file=sys.stderr)
        _emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
