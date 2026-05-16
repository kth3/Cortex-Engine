"""cortex-ctl migrate sub-command — move legacy <ws>/.cortex/data into global ~/.cortex/workspaces/<key>/.

Legacy layout (before 단계 2):
    <workspace>/.cortex/data/memories.db
    <workspace>/.cortex/data/graph_db_store/
    <workspace>/.cortex/history/

Target layout:
    ~/.cortex/workspaces/<workspace_key>/memories.db
    ~/.cortex/workspaces/<workspace_key>/graph_db_store/
    ~/.cortex/workspaces/<workspace_key>/history/
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from cortex.paths import resolve_workspace, workspace_data_dir

CORTEX_DIRNAME = ".cortex"
LEGACY_DATA_DIRNAME = "data"
LEGACY_HISTORY_DIRNAME = "history"
LEGACY_ITEMS = ("memories.db", "graph_db_store", "history")


def _legacy_root_from(workspace: Path) -> Path:
    resolved = workspace.resolve()
    parts = resolved.parts
    if CORTEX_DIRNAME in parts:
        idx = parts.index(CORTEX_DIRNAME)
        return Path(*parts[: idx + 1])
    return resolved / CORTEX_DIRNAME


def _legacy_items(cortex_dir: Path) -> dict[str, Path]:
    """Return present legacy items keyed by their final relocation name."""
    return {
        "memories.db": cortex_dir / LEGACY_DATA_DIRNAME / "memories.db",
        "graph_db_store": cortex_dir / LEGACY_DATA_DIRNAME / "graph_db_store",
        "history": cortex_dir / LEGACY_HISTORY_DIRNAME,
    }


def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    shutil.move(str(src), str(dest))


def _cleanup_empty_legacy_dirs(cortex_dir: Path) -> None:
    legacy_data = cortex_dir / LEGACY_DATA_DIRNAME
    if legacy_data.is_dir() and not any(legacy_data.iterdir()):
        legacy_data.rmdir()


def _run_migrate(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser() if args.source else resolve_workspace()
    cortex_dir = _legacy_root_from(source)
    if not cortex_dir.exists():
        print(
            json.dumps(
                {"action": "migrate", "status": "no-cortex-dir", "source": str(cortex_dir)},
                ensure_ascii=False,
            )
        )
        return 0

    workspace_root = cortex_dir.parent
    target_dir = workspace_data_dir(workspace_root)

    items = _legacy_items(cortex_dir)
    present = {name: path for name, path in items.items() if path.exists()}
    if not present:
        print(
            json.dumps(
                {
                    "action": "migrate",
                    "status": "noop",
                    "source": str(cortex_dir),
                    "target": str(target_dir),
                    "note": "no legacy items to migrate",
                },
                ensure_ascii=False,
            )
        )
        return 0

    plan: list[dict[str, str]] = []
    conflicts: list[str] = []
    for name, src in present.items():
        dest = target_dir / name
        plan.append({"name": name, "source": str(src), "target": str(dest)})
        if dest.exists() and not args.force:
            conflicts.append(name)

    if conflicts and not args.force:
        print(
            json.dumps(
                {
                    "action": "migrate",
                    "status": "conflict",
                    "conflicts": conflicts,
                    "target": str(target_dir),
                    "hint": "rerun with --force to overwrite target items",
                    "plan": plan,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "migrate",
                    "status": "dry-run",
                    "target": str(target_dir),
                    "plan": plan,
                },
                ensure_ascii=False,
            )
        )
        return 0

    moved: list[str] = []
    for name, src in present.items():
        dest = target_dir / name
        _move(src, dest)
        moved.append(name)

    _cleanup_empty_legacy_dirs(cortex_dir)

    print(
        json.dumps(
            {
                "action": "migrate",
                "status": "ok",
                "moved": moved,
                "source": str(cortex_dir),
                "target": str(target_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-ctl migrate",
        description="Move legacy <ws>/.cortex/data into ~/.cortex/workspaces/<key>/.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Workspace path to migrate (defaults to detected workspace).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan without moving files.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing items at the target.")
    parser.set_defaults(handler=_run_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)
