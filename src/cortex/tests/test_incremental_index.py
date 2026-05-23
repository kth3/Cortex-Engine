from __future__ import annotations

from cortex.indexing import incremental as incremental_module


def test_incremental_index_changed_wraps_rust_workspace_index(monkeypatch):
    calls = []
    monkeypatch.setattr(incremental_module, "_last_opportunistic_check", 0.0)
    monkeypatch.setattr(
        incremental_module,
        "index_workspace",
        lambda workspace, force=False: calls.append((workspace, force)) or {
            "total_files": 4,
            "indexed": 2,
            "skipped": 2,
            "errors": 0,
            "deleted": 1,
        },
    )

    result = incremental_module.incremental_index_changed("workspace")

    assert calls == [("workspace", False)]
    assert result == {"status": "indexed", "changed": 3, "indexed": 2}


def test_incremental_index_changed_returns_clean_when_rust_reports_no_changes(monkeypatch):
    monkeypatch.setattr(incremental_module, "_last_opportunistic_check", 0.0)
    monkeypatch.setattr(
        incremental_module,
        "index_workspace",
        lambda workspace, force=False: {
            "total_files": 4,
            "indexed": 0,
            "skipped": 4,
            "errors": 0,
            "deleted": 0,
        },
    )

    result = incremental_module.incremental_index_changed("workspace")

    assert result == {"status": "clean", "checked_files": 4}
