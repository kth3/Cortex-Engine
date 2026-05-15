"""Global Codex hook installer and runtime adapter for Cortex."""
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
from cortex.mcp.tools.search import call_pc_capsule
from cortex.mcp.tools.session import call_pc_auto_context

HOOKS_JSON_FILENAME = "hooks.json"
HOOKS_DIRNAME = "hooks"
LAUNCHER_FILENAME = "cortex_codex_hook.py"
DEFAULT_PROFILE = "safe"
DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_SESSION_ID = "codex-hook"
DEFAULT_HOOK_TIMEOUT_SECONDS = 45

EVENT_SESSION_START = "SessionStart"
EVENT_USER_PROMPT_SUBMIT = "UserPromptSubmit"
SUPPORTED_RUN_EVENTS = (EVENT_SESSION_START, EVENT_USER_PROMPT_SUBMIT)

HOOK_MARKER = "cortex_codex_hook.py"


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


def _hooks_dir(codex_home: Path) -> Path:
    return codex_home / HOOKS_DIRNAME


def _launcher_path(codex_home: Path) -> Path:
    return _hooks_dir(codex_home) / LAUNCHER_FILENAME


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
    print(f"[Cortex Codex hook skipped unsupported event: {event_name}]", file=sys.stderr)
    return {}


def _launcher_source() -> str:
    return r'''#!/usr/bin/env python3
"""Global launcher installed by cortex-codex-hook."""
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
    normalized = raw.lstrip("\ufeff")
    if not normalized.strip():
        return {}, raw
    try:
        payload = json.loads(normalized)
    except Exception as exc:
        print(f"[Cortex hook launcher ignored invalid JSON: {exc}]", file=sys.stderr)
        return {}, raw
    return payload if isinstance(payload, dict) else {}, raw


def _codex_home() -> Path:
    return Path(__file__).resolve().parents[1]


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

    codex_home = _codex_home()
    cache_dir = os.environ.get("CORTEX_UV_CACHE_DIR") or str(codex_home / ".uv-cache-local")
    uv_command = os.environ.get("CORTEX_UV_COMMAND") or "uv"
    command = [
        uv_command,
        "--cache-dir",
        cache_dir,
        "run",
        "--project",
        str(cortex_home),
        "cortex-codex-hook",
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


def _event_status_message(event_name: str) -> str:
    if event_name == EVENT_SESSION_START:
        return "Loading Cortex context"
    return "Searching Cortex context"


def _install_event_hook(
    data: dict[str, Any],
    event_name: str,
    command: str,
    timeout: int,
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
                return

    event_groups.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": timeout,
                    "statusMessage": _event_status_message(event_name),
                }
            ]
        }
    )


def _install_events(include_user_prompt_submit: bool) -> list[str]:
    events = [EVENT_SESSION_START]
    if include_user_prompt_submit:
        events.append(EVENT_USER_PROMPT_SUBMIT)
    return events


def install_hooks(args: argparse.Namespace) -> dict[str, Any]:
    codex_home = _codex_home(args.codex_home)
    hooks_dir = _hooks_dir(codex_home)
    launcher = _launcher_path(codex_home)
    hooks_json = _hooks_json_path(codex_home)
    events = _install_events(args.include_user_prompt_submit)

    data = _load_hooks_json(hooks_json)
    for event_name in events:
        _install_event_hook(
            data,
            event_name,
            _hook_command(launcher, event_name, args.python_command),
            args.timeout,
        )

    result = {
        "codexHome": str(codex_home),
        "launcher": str(launcher),
        "hooksJson": str(hooks_json),
        "events": events,
        "hooks": data,
        "dryRun": bool(args.dry_run),
    }

    if args.dry_run:
        return result

    hooks_dir.mkdir(parents=True, exist_ok=True)
    launcher.write_text(_launcher_source(), encoding="utf-8")
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

    install_parser = subparsers.add_parser("install", help="Install global Codex hook launcher and hooks.json entries.")
    install_parser.add_argument("--codex-home", default=None)
    install_parser.add_argument("--profile", choices=(DEFAULT_PROFILE,), default=DEFAULT_PROFILE)
    install_parser.add_argument("--include-user-prompt-submit", action="store_true")
    install_parser.add_argument("--python-command", default=sys.executable)
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
