import os
import pytest
import sqlite3
from scripts.cortex.skill_manager import SkillManager
from scripts.cortex import db

# We need to configure the sqlite3 row factory to return dict-like objects
# before init_schema is called, or modify our mock to let it succeed.
# Wait, actually, skill_manager uses plain sqlite3 connection which doesn't
# have row_factory set by default. But init_schema uses dict-like indexing.
# Let's override sqlite3.connect to return a connection with row_factory set.

original_connect = sqlite3.connect

def mock_connect(*args, **kwargs):
    conn = original_connect(*args, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn

def test_n_plus_one_benchmark(benchmark, mocker, tmp_path):
    mocker.patch('sqlite3.connect', side_effect=mock_connect)

    workspace = str(tmp_path)
    sm = SkillManager(workspace)

    # Insert 500 fake skills into the DB
    db_path = os.path.join(workspace, ".agents/cortex_data/index.db")
    conn = mock_connect(db_path)
    for i in range(500):
        skill_key = f"skill::mock_{i}"
        conn.execute("INSERT INTO memories (key, project_id, category, content, tags, created_at, updated_at) VALUES (?, ?, 'skill', ?, ?, ?, ?)",
                     (skill_key, "proj", f"content {i}", "[]", 0, 0))
    conn.commit()
    conn.close()

    def mock_search_similar(*args, **kwargs):
        return [{"id": f"skill::mock_{i}"} for i in range(500)]

    mocker.patch('scripts.cortex.skill_manager.ve.search_similar', side_effect=mock_search_similar)

    def run_search():
        sm.search_skills("proj", "nothing", limit=500)

    benchmark(run_search)
