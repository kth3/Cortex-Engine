"""Global Claude Code hook installer and runtime adapter for Cortex."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from cortex.mcp.context import McpContext
from cortex.mcp.tools.memory import call_save_observation
from cortex.mcp.tools.search import call_pc_capsule, call_pc_skeleton
from cortex.mcp.tools.session import call_pc_auto_context, call_pc_session_sync

SETTINGS_FILENAME = "settings.json"
HOOKS_DIRNAME = "hooks"
LAUNCHER_FILENAME = "cortex_claude_hook.py"
DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_SESSION_ID = "claude-hook"
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

EDIT_TOOL_MATCHER = "Edit|Write|MultiEdit"
EDIT_TOOL_NAMES = {"Edit", "Write", "MultiEdit"}

HOOK_MARKER = "cortex_claude_hook.py"


def _empty_output() -> str:
    return "{}"


def _emit_empty() -> None:
    print(_empty_output())


def _claude_home(raw_claude_home: str | None = None) -> Path:
    if raw_claude_home:
        return Path(raw_claude_home).expanduser().resolve()
    env_home = os.environ.get("CLAUDE_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def _settings_path(claude_home: Path) -> Path:
    return claude_home / SETTINGS_FILENAME


def _hooks_dir(claude_home: Path) -> Path:
    return claude_home / HOOKS_DIRNAME


def _launcher_path(claude_home: Path) -> Path:
    return _hooks_dir(claude_home) / LAUNCHER_FILENAME


def _read_stdin_json() -> tuple[dict[str, Any], str]:
    raw = sys.stdin.read()
    normalized = raw.lstrip("﻿")
    if not normalized.strip():
        return {}, raw
    try:
        data = json.loads(normalized)
    except Exception as exc:
        print(f"[Cortex Claude hook ignored invalid JSON: {exc}]", file=sys.stderr)
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
        or os.environ.get("CLAUDE_SESSION_ID")
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
            f"[Cortex Claude hook skipped: .cortex not found for {workspace}]",
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


def _last_assistant_from_transcript(transcript_path: str | None) -> str:
    if not transcript_path:
        return ""
    try:
        path = Path(transcript_path).expanduser()
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    last = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue

        message = obj.get("message")
        if obj.get("type") == "assistant" and isinstance(message, dict):
            content_blocks = message.get("content")
            if isinstance(content_blocks, list):
                texts = [
                    str(block.get("text", ""))
                    for block in content_blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                joined = "\n".join(t for t in texts if t).strip()
                if joined:
                    last = joined
                    continue

        if obj.get("role") == "assistant":
            raw_content = obj.get("content")
            if isinstance(raw_content, str) and raw_content.strip():
                last = raw_content.strip()
    return last


def _run_stop(ctx: McpContext, payload: dict[str, Any]) -> dict[str, Any]:
    task_desc = _last_assistant_from_transcript(payload.get("transcript_path"))
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
    for key in ("file_path", "path", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _run_pre_tool_use(ctx: McpContext, payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    if tool_name not in EDIT_TOOL_NAMES:
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
    if tool_name not in EDIT_TOOL_NAMES:
        return {}
    file_path = _tool_input_file_path(payload.get("tool_input"))
    if not file_path:
        return {}
    content = f"{tool_name} edited {file_path}"
    session_id = payload.get("session_id")
    if session_id:
        content += f" (session={session_id})"
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
    print(f"[Cortex Claude hook skipped unsupported event: {event_name}]", file=sys.stderr)
    return {}


def _launcher_source() -> str:
    return r'''#!/usr/bin/env python3
"""Global launcher installed by cortex-claude-hook."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LAUNCHER_TIMEOUT_SECONDS = 55


def _emit_empty() -> None:
    print("{}")


def _read_payload() -> tuple[dict, str]:
    raw = sys.stdin.read()
    normalized = raw.lstrip("﻿")
    if not normalized.strip():
        return {}, raw
    try:
        payload = json.loads(normalized)
    except Exception as exc:
        print(f"[Cortex hook launcher ignored invalid JSON: {exc}]", file=sys.stderr)
        return {}, raw
    return payload if isinstance(payload, dict) else {}, raw


def _find_cortex_home_from(start_path: str | None) -> Path | None:
    if not start_path:
        return None
    curr = Path(start_path).expanduser().resolve()
    for base in (curr, *curr.parents):
        if base.name == ".cortex" and (base / "pyproject.toml").exists():
            return base
        candidate = base / ".cortex"
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return None


def _resolve_cortex_home(payload: dict) -> Path | None:
    from_payload = _find_cortex_home_from(payload.get("cwd"))
    if from_payload is not None:
        return from_payload

    env_home = os.environ.get("CORTEX_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    return _find_cortex_home_from(os.getcwd())


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    payload, raw = _read_payload()
    if not event_name:
        event_name = str(payload.get("hook_event_name") or "")
    if not event_name:
        _emit_empty()
        return 0

    cortex_home = _resolve_cortex_home(payload)
    if cortex_home is None:
        print("[Cortex hook launcher skipped: .cortex not found]", file=sys.stderr)
        _emit_empty()
        return 0

    uv_command = os.environ.get("CORTEX_UV_COMMAND") or "uv"
    command = [uv_command]
    override_cache = os.environ.get("CORTEX_UV_CACHE_DIR")
    if override_cache:
        command += ["--cache-dir", override_cache]
    command += [
        "run",
        "--project",
        str(cortex_home),
        "cortex-claude-hook",
        "run",
        event_name,
    ]
    try:
        proc = subprocess.run(
            command,
            input=raw,
            text=True,
            capture_output=True,
            timeout=LAUNCHER_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        print(f"[Cortex hook launcher failed: {exc}]", file=sys.stderr)
        _emit_empty()
        return 0

    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    stdout = proc.stdout.strip()
    print(stdout if stdout else "{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _hook_command(launcher: Path, event_name: str, python_command: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([python_command, str(launcher), event_name])
    return " ".join(
        [
            shlex.quote(python_command),
            shlex.quote(str(launcher)),
            shlex.quote(event_name),
        ]
    )


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def _is_cortex_hook(handler: dict[str, Any], event_name: str) -> bool:
    command = str(handler.get("command") or "")
    return HOOK_MARKER in command and event_name in command


def _install_event_hook(
    data: dict[str, Any],
    event_name: str,
    command: str,
    timeout: int,
    matcher: str | None = None,
) -> None:
    hooks_root = data.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        hooks_root = {}
        data["hooks"] = hooks_root
    event_groups = hooks_root.setdefault(event_name, [])
    if not isinstance(event_groups, list):
        event_groups = []
        hooks_root[event_name] = event_groups

    for group in event_groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks", [])
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if isinstance(handler, dict) and _is_cortex_hook(handler, event_name):
                handler.update(
                    {
                        "type": "command",
                        "command": command,
                        "timeout": timeout,
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
        pairs.append((EVENT_PRE_TOOL_USE, EDIT_TOOL_MATCHER))
    if include_all or getattr(args, "include_post_tool_use", False):
        pairs.append((EVENT_POST_TOOL_USE, EDIT_TOOL_MATCHER))
    return pairs


def install_hooks(args: argparse.Namespace) -> dict[str, Any]:
    claude_home = _claude_home(args.claude_home)
    hooks_dir = _hooks_dir(claude_home)
    launcher = _launcher_path(claude_home)
    settings_file = _settings_path(claude_home)
    event_pairs = _install_events(args)

    data = _load_settings(settings_file)
    for event_name, matcher in event_pairs:
        _install_event_hook(
            data,
            event_name,
            _hook_command(launcher, event_name, args.python_command),
            args.timeout,
            matcher=matcher,
        )

    result = {
        "claudeHome": str(claude_home),
        "launcher": str(launcher),
        "settingsFile": str(settings_file),
        "events": [event_name for event_name, _ in event_pairs],
        "settings": data,
        "dryRun": bool(args.dry_run),
    }

    if args.dry_run:
        return result

    hooks_dir.mkdir(parents=True, exist_ok=True)
    launcher.write_text(_launcher_source(), encoding="utf-8")
    settings_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and run Cortex Claude Code hooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a Cortex adapter for one Claude Code hook event.")
    run_parser.add_argument("event", choices=SUPPORTED_RUN_EVENTS)
    run_parser.add_argument("--workspace", default=None)
    run_parser.add_argument("--cortex-home", default=None)
    run_parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)

    install_parser = subparsers.add_parser("install", help="Install global Claude Code hook launcher and settings.json entries.")
    install_parser.add_argument("--claude-home", default=None)
    install_parser.add_argument("--include-user-prompt-submit", action="store_true")
    install_parser.add_argument("--include-stop", action="store_true")
    install_parser.add_argument("--include-pre-tool-use", action="store_true")
    install_parser.add_argument("--include-post-tool-use", action="store_true")
    install_parser.add_argument(
        "--include-all",
        action="store_true",
        help="Install every supported event in addition to SessionStart.",
    )
    install_parser.add_argument("--python-command", default=sys.executable)
    install_parser.add_argument("--timeout", type=int, default=DEFAULT_HOOK_TIMEOUT_SECONDS)
    install_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "install":
        print(json.dumps(install_hooks(args), ensure_ascii=False))
        return 0

    payload, _raw = _read_stdin_json()
    try:
        print(
            json.dumps(
                run_event(
                    args.event,
                    payload,
                    raw_workspace=args.workspace,
                    raw_cortex_home=args.cortex_home,
                    token_budget=args.token_budget,
                ),
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        print(f"[Cortex Claude hook unavailable: {exc}]", file=sys.stderr)
        _emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
