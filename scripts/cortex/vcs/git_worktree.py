"""Git worktree helpers for isolated Cortex workspaces."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitWorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


def _run_git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise GitWorktreeError(detail) from exc


def git_root(workspace: str | Path) -> Path:
    """Return the repository root for a Git workspace."""
    workspace_path = Path(workspace).resolve()
    result = _run_git(workspace_path, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def create_detached_worktree(main_workspace: str | Path, target_dir: str | Path) -> Path:
    """Create a detached worktree at target_dir based on main_workspace HEAD."""
    main_root = git_root(main_workspace)
    target_path = Path(target_dir).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        git_root(target_path)
        return target_path

    _run_git(main_root, ["worktree", "add", "--detach", str(target_path), "HEAD"])
    return target_path


def is_worktree_dirty(worktree_path: str | Path) -> bool:
    """Return True if the worktree has tracked or untracked changes."""
    root = git_root(worktree_path)
    result = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    return bool(result.stdout.strip())


def remove_worktree(main_workspace: str | Path, worktree_path: str | Path) -> None:
    """Remove a clean worktree without forcing deletion."""
    target_path = Path(worktree_path).resolve()
    if not target_path.exists():
        return
    main_root = git_root(main_workspace)
    _run_git(main_root, ["worktree", "remove", str(target_path)])
