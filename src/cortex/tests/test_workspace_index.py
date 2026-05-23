from __future__ import annotations

from types import SimpleNamespace

from cortex.indexing import workspace as workspace_module


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self

    def commit(self):
        self.executed.append(("commit", None))

    def close(self):
        self.closed = True


def test_index_workspace_uses_rust_report_and_python_postprocessing(monkeypatch):
    calls = []
    fake_conn = _FakeConn()
    rust_report = {
        "total_files": 3,
        "indexed": 2,
        "skipped": 1,
        "errors": 0,
        "deleted": 1,
        "vector_items_by_prefix": {
            "root": [{"id": "node-1", "text": "text-1", "meta": {"file": "a.py"}}]
        },
    }

    monkeypatch.setattr(workspace_module, "_sync_skills", lambda workspace: calls.append(("sync_skills", workspace)))
    monkeypatch.setattr(workspace_module, "_run_rust_index", lambda workspace, force: calls.append(("rust_index", workspace, force)) or rust_report)
    monkeypatch.setattr(workspace_module.db, "get_connection", lambda workspace: fake_conn)
    monkeypatch.setattr(workspace_module.db, "init_schema", lambda conn: calls.append(("init_schema", conn)))
    monkeypatch.setattr(workspace_module, "detect_gpu", lambda: False)
    monkeypatch.setattr(
        workspace_module,
        "batch_vectorize_nodes",
        lambda conn, items_by_prefix, use_gpu, workspace=None: calls.append(("vector_nodes", items_by_prefix, use_gpu, workspace)),
    )
    monkeypatch.setattr(
        workspace_module,
        "sync_rules_to_memories",
        lambda workspace, conn: calls.append(("sync_rules", workspace, conn)),
    )
    monkeypatch.setattr(
        workspace_module,
        "batch_vectorize_memories",
        lambda conn, use_gpu, workspace=None: calls.append(("vector_memories", use_gpu, workspace)),
    )
    monkeypatch.setattr(
        workspace_module,
        "_release_local_cuda_model_after_indexing",
        lambda: calls.append(("release_cuda",)),
    )
    monkeypatch.setattr(
        workspace_module,
        "_sync_graph_from_sqlite",
        lambda workspace, conn: calls.append(("graph_sync", workspace, conn)),
    )

    result = workspace_module.index_workspace("C:/workspace", force=True)

    assert result == {
        "total_files": 3,
        "indexed": 2,
        "skipped": 1,
        "errors": 0,
        "deleted": 1,
    }
    assert calls[0] == ("sync_skills", "C:/workspace")
    assert calls[1] == ("rust_index", "C:/workspace", True)
    assert calls[2][0] == "init_schema"
    assert calls[3] == ("vector_nodes", rust_report["vector_items_by_prefix"], False, "C:/workspace")
    assert calls[4][0] == "sync_rules"
    assert calls[5] == ("vector_memories", False, "C:/workspace")
    assert calls[6] == ("release_cuda",)
    assert fake_conn.executed[-2][0].startswith("INSERT OR REPLACE INTO meta")
    assert fake_conn.executed[-1] == ("commit", None)
    assert calls[7] == ("graph_sync", "C:/workspace", fake_conn)
    assert fake_conn.closed is True
