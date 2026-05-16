"""Test fixtures shared across the Cortex test suite.

Isolates CORTEX_DATA_HOME so that tests never read or write the
user's real ~/.cortex directory, and stubs hook entry resolution so
tests don't depend on PATH having cortex-codex-hook installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

collect_ignore = ["test_mcp_smoke.py"]

_FAKE_CODEX_HOOK = Path("/tmp/fake-bin/cortex-codex-hook")
_FAKE_CLAUDE_HOOK = Path("/tmp/fake-bin/cortex-claude-hook")


@pytest.fixture(autouse=True)
def _isolate_cortex_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_DATA_HOME", str(tmp_path / "cortex-data-home"))
    monkeypatch.delenv("CORTEX_WORKSPACE_KEY", raising=False)
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)
    yield


@pytest.fixture(autouse=True)
def _stub_hook_command_resolver(monkeypatch):
    from cortex.integrations import claude_hook, codex_hook

    monkeypatch.setattr(codex_hook, "_default_hook_command_path", lambda: _FAKE_CODEX_HOOK)
    monkeypatch.setattr(claude_hook, "_default_hook_command_path", lambda: _FAKE_CLAUDE_HOOK)
    yield
