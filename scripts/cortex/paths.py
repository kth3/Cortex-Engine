"""Shared Cortex path resolution helpers."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_CORTEX_HOME_NAME = ".cortex"
CORTEX_HOME_NAMES = (DEFAULT_CORTEX_HOME_NAME,)
WORKSPACES_DIRNAME = "workspaces"
HISTORY_DIRNAME = "history"
WORKSPACE_KEY_PREFIX_LEN = 12


def resolve_workspace(start_path: str | os.PathLike | None = None) -> Path:
    env_ws = os.environ.get("CORTEX_WORKSPACE")
    if env_ws:
        return Path(env_ws).resolve()

    curr = Path(start_path or os.getcwd()).resolve()

    for parent in (curr, *curr.parents):
        if (parent / ".git").exists():
            return parent
    return curr


def resolve_cortex_home(workspace: str | os.PathLike | None = None) -> Path:
    env_home = os.environ.get("CORTEX_HOME")
    if env_home:
        return Path(env_home).resolve()

    base = Path(workspace or os.getcwd()).resolve()
    workspace_local_home = base / DEFAULT_CORTEX_HOME_NAME
    if workspace_local_home.exists():
        return workspace_local_home.resolve()

    for name in CORTEX_HOME_NAMES:
        if name in base.parts:
            # 중첩된 임시 워크스페이스에서는 가장 가까운 .cortex가 해당 워크스페이스의 홈이다.
            idx = len(base.parts) - 1 - list(reversed(base.parts)).index(name)
            return Path(*base.parts[: idx + 1])

    return (Path.home() / DEFAULT_CORTEX_HOME_NAME).resolve()


def data_home() -> Path:
    env_home = os.environ.get("CORTEX_DATA_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / DEFAULT_CORTEX_HOME_NAME).resolve()


def workspace_key(workspace: str | os.PathLike | None = None) -> str:
    override = os.environ.get("CORTEX_WORKSPACE_KEY")
    if override:
        return override
    ws = Path(workspace or os.getcwd()).resolve()
    return hashlib.sha1(str(ws).encode("utf-8")).hexdigest()[:WORKSPACE_KEY_PREFIX_LEN]


def workspace_data_dir(workspace: str | os.PathLike | None = None) -> Path:
    path = data_home() / WORKSPACES_DIRNAME / workspace_key(workspace)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir(workspace: str | os.PathLike | None = None) -> Path:
    return workspace_data_dir(workspace)


def history_dir(workspace: str | os.PathLike | None = None) -> Path:
    path = workspace_data_dir(workspace) / HISTORY_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_paths(workspace: str | os.PathLike | None = None) -> tuple[Path, Path]:
    home = resolve_cortex_home(workspace)
    return home / "settings.yaml", home / "settings.local.yaml"
