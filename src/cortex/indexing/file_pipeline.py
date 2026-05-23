"""Single-file indexing pipeline."""
from __future__ import annotations

import json
import subprocess

from cortex.runtime.paths import ensure_rust_watcher_binary


def _run_rust_index_file(workspace: str, rel_path: str, force: bool) -> dict:
    binary = ensure_rust_watcher_binary()
    command = [
        str(binary),
        "index-file",
        "--workspace",
        workspace,
        "--file",
        rel_path,
    ]
    if force:
        command.append("--force")

    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() if exc.stderr else str(exc)
        return {"error": message}
    return json.loads(proc.stdout or "{}")


def index_file(
    workspace: str,
    rel_path: str,
    conn=None,
    vectorize: bool = True,
    use_gpu: bool | None = None,
    source_path: str | None = None,
):
    """단일 파일 인덱싱은 Rust 워커에 위임한다."""
    if conn is not None:
        try:
            conn.commit()
        except Exception:
            pass
    result = _run_rust_index_file(workspace, rel_path, force=False)
    if conn is not None:
        try:
            conn.commit()
        except Exception:
            pass
    return result
