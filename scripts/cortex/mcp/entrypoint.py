"""Cortex MCP console entrypoint with Rust-first fallback."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _rust_mcp_binary_name() -> str:
    return "cortex-mcp.exe" if os.name == "nt" else "cortex-mcp"


def _resolve_rust_mcp_binary() -> Path | None:
    repo_root = _resolve_repo_root()
    rust_target_dir = repo_root / "rust" / "target"
    binary_name = _rust_mcp_binary_name()
    for candidate in (
        rust_target_dir / "release" / binary_name,
        rust_target_dir / "debug" / binary_name,
    ):
        if candidate.exists():
            return candidate
    return None


def _run_python_server() -> None:
    from cortex.mcp.server import main

    main()


def _run_rust_binary(binary: Path) -> int:
    env = os.environ.copy()
    env.setdefault("CORTEX_PYTHON_EXECUTABLE", sys.executable)
    completed = subprocess.run(
        [str(binary), *sys.argv[1:]],
        cwd=str(_resolve_repo_root()),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
        check=False,
    )
    return completed.returncode


def main() -> None:
    if os.environ.get("CORTEX_MCP_FORCE_PYTHON") == "1":
        _run_python_server()
        return

    binary = _resolve_rust_mcp_binary()
    if binary is not None:
        try:
            if _run_rust_binary(binary) == 0:
                return
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass

    _run_python_server()
