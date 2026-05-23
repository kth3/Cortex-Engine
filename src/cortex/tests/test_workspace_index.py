from __future__ import annotations

from cortex.indexing import workspace as workspace_module


def test_index_workspace_returns_rust_report(monkeypatch):
    calls = []
    rust_report = {
        "total_files": 3,
        "indexed": 2,
        "skipped": 1,
        "errors": 0,
        "deleted": 1,
        "vector_items_by_prefix": {"root": [{"id": "node-1"}]},
    }

    monkeypatch.setattr(
        workspace_module,
        "_run_rust_index",
        lambda workspace, force: calls.append((workspace, force)) or rust_report,
    )

    result = workspace_module.index_workspace("workspace", force=True)

    assert calls == [("workspace", True)]
    assert result == {
        "total_files": 3,
        "indexed": 2,
        "skipped": 1,
        "errors": 0,
        "deleted": 1,
    }
