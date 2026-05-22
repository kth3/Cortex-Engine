"""Cortex MCP console entrypoint."""
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


def _run_rust_binary(binary: Path) -> int:
    completed = subprocess.run(
        [str(binary), *sys.argv[1:]],
        cwd=str(_resolve_repo_root()),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=False,
    )
    return completed.returncode


def _run_cargo_binary() -> int:
    repo_root = _resolve_repo_root()
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(repo_root / "rust" / "Cargo.toml"),
            "-p",
            "cortex-mcp",
            "--bin",
            "cortex-mcp",
            "--",
            *sys.argv[1:],
        ],
        cwd=str(repo_root),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=False,
    )
    return completed.returncode


def main() -> None:
    binary = _resolve_rust_mcp_binary()
    if binary is not None:
        raise SystemExit(_run_rust_binary(binary))

    raise SystemExit(_run_cargo_binary())
