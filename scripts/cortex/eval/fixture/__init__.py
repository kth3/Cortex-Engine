"""Evaluation fixture — 저장소 동봉 노드·메모리 데이터.

이 패키지의 yaml 파일은 모든 사용자가 동일하게 인덱싱·평가할 수 있도록 고정된
입력 데이터를 제공한다. 사용자 워크스페이스 콘텐츠는 평가에 사용하지 않는다.
"""

from cortex.eval.fixture.loader import (
    FIXTURE_PROJECT_ID,
    NODES_YAML,
    MEMORIES_YAML,
    load_fixture_nodes,
    load_fixture_memories,
    insert_fixture_nodes,
    insert_fixture_memories,
    setup_fixture_db,
)

__all__ = [
    "FIXTURE_PROJECT_ID",
    "NODES_YAML",
    "MEMORIES_YAML",
    "load_fixture_nodes",
    "load_fixture_memories",
    "insert_fixture_nodes",
    "insert_fixture_memories",
    "setup_fixture_db",
]
