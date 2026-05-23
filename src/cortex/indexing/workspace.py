"""Workspace indexing pipeline."""
from __future__ import annotations

import json
import subprocess

from cortex.logger import get_logger
from cortex.runtime.paths import ensure_rust_watcher_binary

log = get_logger("indexer")


def _run_rust_index(workspace: str, force: bool) -> dict:
    binary = ensure_rust_watcher_binary()
    command = [
        str(binary),
        "index",
        "--workspace",
        workspace,
    ]
    if force:
        command.append("--force")

    proc = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout or "{}")


def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 인덱싱은 Rust 워커 결과를 그대로 반환한다."""
    report = _run_rust_index(workspace, force=force)
    return {
        "total_files": int(report.get("total_files", 0)),
        "indexed": int(report.get("indexed", 0)),
        "skipped": int(report.get("skipped", 0)),
        "errors": int(report.get("errors", 0)),
        "deleted": int(report.get("deleted", 0)),
    }
