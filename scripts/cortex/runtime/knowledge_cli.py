"""knowledge.zip enable/disable/status sub-command for cortex-ctl."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

from cortex.paths import resolve_workspace

KNOWLEDGE_ZIP_FILENAME = "knowledge.zip"
KNOWLEDGE_DIRNAME = "knowledge"
CORTEX_DIRNAME = ".cortex"
SEED_SUBDIRS = ("resources", "examples", "skills")


def _cortex_home_for(workspace: Path) -> Path:
    resolved = workspace.resolve()
    parts = resolved.parts
    if CORTEX_DIRNAME in parts:
        idx = parts.index(CORTEX_DIRNAME)
        return Path(*parts[: idx + 1])
    return resolved / CORTEX_DIRNAME


def _knowledge_root(workspace: Path) -> Path:
    return _cortex_home_for(workspace) / KNOWLEDGE_DIRNAME


def _knowledge_zip(workspace: Path) -> Path:
    return _knowledge_root(workspace) / KNOWLEDGE_ZIP_FILENAME


def _expanded_subdirs(root: Path) -> list[Path]:
    return [root / name for name in SEED_SUBDIRS if (root / name).is_dir()]


def _count_files(root: Path) -> int:
    return sum(1 for entry in root.rglob("*") if entry.is_file())


def _enable(args: argparse.Namespace, workspace: Path) -> int:
    root = _knowledge_root(workspace)
    zip_path = _knowledge_zip(workspace)
    if not zip_path.exists():
        print(f"[knowledge] zip not found: {zip_path}", file=sys.stderr)
        return 1

    existing = _expanded_subdirs(root)
    if existing and not args.force:
        names = ", ".join(p.name for p in existing)
        print(
            json.dumps(
                {
                    "action": "enable",
                    "status": "already-expanded",
                    "expanded": names,
                    "hint": "rerun with --force to overwrite",
                },
                ensure_ascii=False,
            )
        )
        return 0

    if existing and args.force:
        for sub in existing:
            shutil.rmtree(sub, ignore_errors=True)

    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(root)

    counts = {
        name: _count_files(root / name)
        for name in SEED_SUBDIRS
        if (root / name).is_dir()
    }
    print(
        json.dumps(
            {
                "action": "enable",
                "status": "ok",
                "knowledge_root": str(root),
                "expanded": counts,
                "hint": "watcher will index new files; run cortex-index incremental for immediate sync",
            },
            ensure_ascii=False,
        )
    )
    return 0


def _disable(args: argparse.Namespace, workspace: Path) -> int:
    root = _knowledge_root(workspace)
    removed: list[str] = []
    for sub in _expanded_subdirs(root):
        shutil.rmtree(sub, ignore_errors=True)
        removed.append(sub.name)
    print(
        json.dumps(
            {
                "action": "disable",
                "status": "ok" if removed else "noop",
                "removed": removed,
                "knowledge_root": str(root),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _status(args: argparse.Namespace, workspace: Path) -> int:
    root = _knowledge_root(workspace)
    zip_path = _knowledge_zip(workspace)
    expanded: dict[str, int] = {}
    for name in SEED_SUBDIRS:
        sub = root / name
        if sub.is_dir():
            expanded[name] = _count_files(sub)
    result = {
        "knowledge_root": str(root),
        "zip_path": str(zip_path) if zip_path.exists() else None,
        "zip_present": zip_path.exists(),
        "expanded": expanded,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-ctl knowledge",
        description="Manage Cortex knowledge seed expansion.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    enable = sub.add_parser("enable", help="Expand knowledge.zip into the knowledge directory.")
    enable.add_argument("--force", action="store_true", help="Overwrite existing expansion.")
    enable.set_defaults(handler=_enable)

    disable = sub.add_parser("disable", help="Remove expanded knowledge directories (keeps the zip).")
    disable.set_defaults(handler=_disable)

    status = sub.add_parser("status", help="Show knowledge expansion status.")
    status.set_defaults(handler=_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    workspace = resolve_workspace()
    return args.handler(args, workspace)
