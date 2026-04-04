import pytest
from scripts.cortex.persistent_memory import PersistentMemoryManager
import sqlite3

original_connect = sqlite3.connect

def mock_connect(*args, **kwargs):
    conn = original_connect(*args, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn

def test_delete_many_large_parameter_list(tmp_path, monkeypatch):
    """
    Validates proper handling of large parameter lists (exceeding 999) using executemany.
    This bypasses SQLite's maximum variable limit without SQL injection risks.
    """
    monkeypatch.setattr('sqlite3.connect', mock_connect)
    workspace = str(tmp_path)
    pm = PersistentMemoryManager(workspace)
    project_id = "test_project"

    # Insert 1500 records
    for i in range(1500):
        pm.write(project_id, {"key": f"key_{i}", "category": "general", "content": f"data {i}"})

    # Try deleting 1200 records at once
    keys_to_delete = [f"key_{i}" for i in range(1200)]

    deleted_count = pm.delete_many(project_id, keys_to_delete)
    assert deleted_count == 1200

    stats = pm.get_stats(project_id)
    assert stats["total_memories"] == 300

def test_delete_many_sql_injection_resistance(tmp_path, monkeypatch):
    """
    Validates resistance to SQL injection by attempting to inject a subquery or malicious string.
    Since we use executemany with parameterized queries, it should not affect other records.
    """
    monkeypatch.setattr('sqlite3.connect', mock_connect)
    workspace = str(tmp_path)
    pm = PersistentMemoryManager(workspace)
    project_id = "test_project"

    pm.write(project_id, {"key": "safe_key_1", "category": "general", "content": "safe"})
    pm.write(project_id, {"key": "safe_key_2", "category": "general", "content": "safe"})

    # Malicious key that attempts to close the quote and add an OR condition
    malicious_key = "safe_key_1') OR ('1'='1"

    deleted_count = pm.delete_many(project_id, [malicious_key])
    assert deleted_count == 0 # Should not delete anything

    stats = pm.get_stats(project_id)
    assert stats["total_memories"] == 2 # Both original records should still be there
