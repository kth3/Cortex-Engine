"""Tests for global data path policy."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from cortex import paths
from cortex.runtime import paths as runtime_paths


def _watcher_binary_name() -> str:
    return "cortex-watcher.exe" if os.name == "nt" else "cortex-watcher"


def test_data_home_defaults_to_user_dotcortex(monkeypatch, tmp_path):
    monkeypatch.delenv("CORTEX_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert paths.data_home() == (tmp_path / ".cortex").resolve()


def test_data_home_honors_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("CORTEX_DATA_HOME", str(custom))
    assert paths.data_home() == custom.resolve()


def test_workspace_key_is_deterministic_per_path(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    key_a = paths.workspace_key(ws)
    key_b = paths.workspace_key(ws)
    assert key_a == key_b
    assert len(key_a) == paths.WORKSPACE_KEY_PREFIX_LEN


def test_workspace_key_differs_per_path(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert paths.workspace_key(a) != paths.workspace_key(b)


def test_workspace_key_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_WORKSPACE_KEY", "shared-monorepo")
    assert paths.workspace_key(tmp_path / "any") == "shared-monorepo"


def test_workspace_data_dir_lives_under_data_home(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    data = paths.workspace_data_dir(ws)
    assert data.is_dir()
    assert paths.data_home() in data.parents
    assert data.parent.name == paths.WORKSPACES_DIRNAME


def test_data_dir_is_workspace_data_dir(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    assert paths.data_dir(ws) == paths.workspace_data_dir(ws)


def test_history_dir_lives_under_workspace_data_dir(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    h = paths.history_dir(ws)
    assert h.is_dir()
    assert h.parent == paths.workspace_data_dir(ws)
    assert h.name == paths.HISTORY_DIRNAME


def test_multirepo_grouping_via_workspace_key_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_WORKSPACE_KEY", "monorepo")
    ws_a = tmp_path / "repo-a"
    ws_b = tmp_path / "repo-b"
    ws_a.mkdir()
    ws_b.mkdir()
    assert paths.workspace_data_dir(ws_a) == paths.workspace_data_dir(ws_b)


def test_settings_paths_stay_workspace_local(tmp_path):
    cortex_home = tmp_path / ".cortex"
    cortex_home.mkdir()
    main, local = paths.settings_paths(cortex_home)
    assert main == cortex_home / "settings.yaml"
    assert local == cortex_home / "settings.local.yaml"


def test_resolve_rust_watcher_binary_prefers_release(tmp_path, monkeypatch):
    repo_root = tmp_path
    release_dir = repo_root / "rust" / "target" / "release"
    debug_dir = repo_root / "rust" / "target" / "debug"
    release_dir.mkdir(parents=True)
    debug_dir.mkdir(parents=True)

    binary_name = _watcher_binary_name()
    release_binary = release_dir / binary_name
    debug_binary = debug_dir / binary_name
    release_binary.touch()
    debug_binary.touch()

    monkeypatch.setattr(runtime_paths, "REPO_ROOT", repo_root)

    assert runtime_paths.resolve_rust_watcher_binary() == release_binary.resolve()


def test_resolve_rust_watcher_binary_falls_back_to_debug(tmp_path, monkeypatch):
    repo_root = tmp_path
    debug_dir = repo_root / "rust" / "target" / "debug"
    debug_dir.mkdir(parents=True)

    binary_name = _watcher_binary_name()
    debug_binary = debug_dir / binary_name
    debug_binary.touch()

    monkeypatch.setattr(runtime_paths, "REPO_ROOT", repo_root)

    assert runtime_paths.resolve_rust_watcher_binary() == debug_binary.resolve()


def test_ensure_rust_watcher_binary_builds_when_missing(tmp_path, monkeypatch):
    repo_root = tmp_path
    monkeypatch.setattr(runtime_paths, "REPO_ROOT", repo_root)
    mock_run = Mock()
    monkeypatch.setattr(runtime_paths.subprocess, "run", mock_run)

    binary = runtime_paths.ensure_rust_watcher_binary()

    assert binary == (repo_root / "rust" / "target" / "release" / _watcher_binary_name()).resolve()
    mock_run.assert_called_once()
