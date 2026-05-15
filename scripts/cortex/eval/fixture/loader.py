"""Fixture loader — yaml 데이터를 nodes/memories 테이블에 직접 적재한다.

cortex의 일반 인덱서를 우회하므로 임베딩 모델 로드 없이 SQLite FTS 검색만으로
평가를 수행할 수 있다. 벡터 검색 평가는 별도 사이클로 분리.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

FIXTURE_DIR = Path(__file__).parent
NODES_YAML = FIXTURE_DIR / "nodes.yaml"
MEMORIES_YAML = FIXTURE_DIR / "memories.yaml"

FIXTURE_PROJECT_ID = "cortex-eval-fixture"

_NODE_INSERT_SQL = """
INSERT INTO nodes (
    id, type, name, fqn, file_path, start_line, end_line,
    signature, return_type, docstring, is_exported, is_async,
    is_test, raw_body, skeleton_standard, skeleton_minimal,
    language, module, workspace_id, category
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_MEMORY_INSERT_SQL = """
INSERT INTO memories (
    key, project_id, category, content, tags, relationships,
    access_count, created_at, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _read_yaml_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path}: 최상위는 리스트여야 함")
    return data


def load_fixture_nodes() -> list[dict]:
    return _read_yaml_list(NODES_YAML)


def load_fixture_memories() -> list[dict]:
    return _read_yaml_list(MEMORIES_YAML)


def _node_row(node: dict) -> tuple:
    return (
        node["id"],
        node["type"],
        node["name"],
        node["fqn"],
        node["file_path"],
        int(node["start_line"]),
        int(node["end_line"]),
        node.get("signature"),
        node.get("return_type"),
        node.get("docstring"),
        int(node.get("is_exported", 1)),
        int(node.get("is_async", 0)),
        int(node.get("is_test", 0)),
        node.get("raw_body"),
        node.get("skeleton_standard"),
        node.get("skeleton_minimal"),
        node["language"],
        node.get("module", "unknown"),
        node.get("workspace_id", "default"),
        node.get("category", "SOURCE"),
    )


def _memory_row(memory: dict, now_ts: int) -> tuple:
    return (
        memory["key"],
        memory.get("project_id", FIXTURE_PROJECT_ID),
        memory["category"],
        memory["content"],
        json.dumps(memory.get("tags") or [], ensure_ascii=False),
        json.dumps(memory.get("relationships") or {}, ensure_ascii=False),
        int(memory.get("access_count", 0)),
        now_ts,
        now_ts,
    )


def insert_fixture_nodes(conn, nodes: list[dict]) -> None:
    if not nodes:
        return
    conn.executemany(_NODE_INSERT_SQL, [_node_row(n) for n in nodes])


def insert_fixture_memories(conn, memories: list[dict]) -> None:
    if not memories:
        return
    now_ts = int(time.time())
    conn.executemany(_MEMORY_INSERT_SQL, [_memory_row(m, now_ts) for m in memories])


def setup_fixture_db(workspace: str) -> None:
    """워크스페이스의 SQLite DB에 fixture nodes/memories를 적재한다."""
    from cortex.storage import get_connection, init_schema

    conn = get_connection(workspace)
    try:
        init_schema(conn)
        insert_fixture_nodes(conn, load_fixture_nodes())
        insert_fixture_memories(conn, load_fixture_memories())
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "FIXTURE_DIR",
    "NODES_YAML",
    "MEMORIES_YAML",
    "FIXTURE_PROJECT_ID",
    "load_fixture_nodes",
    "load_fixture_memories",
    "insert_fixture_nodes",
    "insert_fixture_memories",
    "setup_fixture_db",
]
