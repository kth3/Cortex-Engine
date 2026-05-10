"""Shared Cortex path resolution helpers."""
from pathlib import Path
import os


AGENT_HOME_NAMES = (".agents", ".cortex")


def _first_agent_home(path: Path) -> Path | None:
    for name in AGENT_HOME_NAMES:
        if name in path.parts:
            idx = path.parts.index(name)
            return Path(*path.parts[:idx + 1])
    return None


def resolve_workspace(start_path: str | os.PathLike | None = None) -> Path:
    env_ws = os.environ.get("CORTEX_WORKSPACE")
    if env_ws:
        return Path(env_ws).resolve()

    curr = Path(start_path or os.getcwd()).resolve()
    agent_home = _first_agent_home(curr)
    if agent_home:
        return agent_home.parent

    for parent in (curr, *curr.parents):
        if (parent / ".git").exists() or any((parent / name).exists() for name in AGENT_HOME_NAMES):
            return parent
    return curr


def resolve_cortex_home(workspace: str | os.PathLike | None = None) -> Path:
    env_home = os.environ.get("CORTEX_HOME")
    if env_home:
        return Path(env_home).resolve()

    base = Path(workspace or os.getcwd()).resolve()
    agent_home = _first_agent_home(base)
    if agent_home:
        return agent_home

    return base / ".agents"


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
