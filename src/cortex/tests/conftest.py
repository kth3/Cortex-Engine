"""Test fixtures shared across the Cortex test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cortex_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_DATA_HOME", str(tmp_path / "cortex-data-home"))
    monkeypatch.delenv("CORTEX_WORKSPACE_KEY", raising=False)
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)
    yield
