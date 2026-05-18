"""cortex-ctl index-roots 명령 구현."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from cortex.config.settings import load_settings
from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.index_roots import (
    add_index_root,
    plan_index_roots_list,
    read_local_settings,
    remove_index_root,
    set_local_index_roots,
)
from cortex.scanner.finder import scan_files


def _workspace_path(raw: str | None) -> str:
    return str(Path(raw or ".").resolve())


def _prepare_settings_home(workspace: str) -> None:
    if os.environ.get("CORTEX_HOME"):
        return
    if (Path(workspace) / ".cortex").exists():
        return

    from cortex.runtime.paths import CORTEX_HOME

    os.environ["CORTEX_HOME"] = str(CORTEX_HOME)


def _scan_count(workspace: str, roots: list[Any]) -> int:
    settings = load_settings(workspace)
    settings.setdefault("indexing_rules", {})["index_roots"] = roots
    return len(scan_files(workspace, SUPPORTED_EXTENSIONS, settings_override=settings))


def _next_command(workspace: str) -> str:
    return f"cortex-index {workspace} --force"


def _print(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _list(args: argparse.Namespace) -> int:
    workspace = _workspace_path(args.workspace)
    _prepare_settings_home(workspace)
    result = plan_index_roots_list(workspace, load_settings(workspace))
    result["workspace"] = workspace
    _print(result)
    return 0


def _add(args: argparse.Namespace) -> int:
    workspace = _workspace_path(args.workspace)
    _prepare_settings_home(workspace)
    settings = load_settings(workspace)
    roots, entry, index_root = add_index_root(workspace, settings, args.path, alias=args.alias)
    scan_count = _scan_count(workspace, roots)

    local_settings, local_path = read_local_settings(workspace)
    if args.execute:
        set_local_index_roots(local_settings, local_path, roots)

    _print(
        {
            "executed": args.execute,
            "workspace": workspace,
            "settings_local": str(local_path),
            "added": entry,
            "normalized_root": index_root.db_root,
            "external": index_root.external,
            "alias": index_root.alias,
            "index_roots": roots,
            "scan_count": scan_count,
            "next_command": _next_command(workspace),
        }
    )
    return 0


def _remove(args: argparse.Namespace) -> int:
    workspace = _workspace_path(args.workspace)
    _prepare_settings_home(workspace)
    settings = load_settings(workspace)
    roots, removed = remove_index_root(workspace, settings, args.target)
    scan_count = _scan_count(workspace, roots)

    local_settings, local_path = read_local_settings(workspace)
    if args.execute:
        set_local_index_roots(local_settings, local_path, roots)

    _print(
        {
            "executed": args.execute,
            "workspace": workspace,
            "settings_local": str(local_path),
            "removed": removed,
            "index_roots": roots,
            "scan_count": scan_count,
            "next_command": _next_command(workspace),
        }
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Cortex indexing roots.")
    sub = parser.add_subparsers(dest="action", required=True)

    list_parser = sub.add_parser("list", help="List configured indexing roots.")
    list_parser.add_argument("--workspace", help="Workspace path. Defaults to current directory.")
    list_parser.set_defaults(func=_list)

    add_parser = sub.add_parser("add", help="Add an indexing root. Dry-run unless --execute is set.")
    add_parser.add_argument("path", help="Path to index. Internal paths stay relative; external paths use an alias.")
    add_parser.add_argument("--workspace", help="Workspace path. Defaults to current directory.")
    add_parser.add_argument("--alias", help="Alias for an external indexing root. Defaults to directory name.")
    add_parser.add_argument("--execute", action="store_true", help="Write settings.local.yaml.")
    add_parser.set_defaults(func=_add)

    remove_parser = sub.add_parser("remove", help="Remove an indexing root. Dry-run unless --execute is set.")
    remove_parser.add_argument("target", help="Internal root path, external alias, or external db root.")
    remove_parser.add_argument("--workspace", help="Workspace path. Defaults to current directory.")
    remove_parser.add_argument("--execute", action="store_true", help="Write settings.local.yaml.")
    remove_parser.set_defaults(func=_remove)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
