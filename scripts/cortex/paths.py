"""Shared Cortex path resolution helpers."""
from pathlib import Path
import os

DEFAULT_CORTEX_HOME_NAME = ".cortex"
CORTEX_HOME_NAMES = (DEFAULT_CORTEX_HOME_NAME,)

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

    # 현재 실행 경로가 .cortex 내부라면 해당 폴더 자체를 반환
    for name in CORTEX_HOME_NAMES:
        if name in base.parts:
            idx = base.parts.index(name)
            return Path(*base.parts[:idx + 1])

    return (Path.home() / DEFAULT_CORTEX_HOME_NAME).resolve()

def data_dir(workspace: str | os.PathLike | None = None) -> Path:
    path = resolve_cortex_home(workspace) / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path

def history_dir(workspace: str | os.PathLike | None = None) -> Path:
    path = resolve_cortex_home(workspace) / "history"
    path.mkdir(parents=True, exist_ok=True)
    return path

def settings_paths(workspace: str | os.PathLike | None = None) -> tuple[Path, Path]:
    home = resolve_cortex_home(workspace)
    return home / "settings.yaml", home / "settings.local.yaml"
