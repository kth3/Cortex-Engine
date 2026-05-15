"""indexing 계층에서 사용하는 SQL 문자열 모음.

retrieval/queries.py가 검색 쿼리 전용인 것처럼, 이 모듈은 indexer와
indexing records 계층이 file_cache, meta, nodes, edges 상태를 읽고
갱신할 때 사용하는 SQL만 관리한다.
SQL 의미, 반환 컬럼 순서, placeholder 개수는 호출부 계약이므로 변경하지 않는다.
"""

UNRESOLVED_FQN_PREFIX = "__unresolved_fqn__::"

FILE_CACHE_HASH_BY_PATH_SQL = "SELECT hash FROM file_cache WHERE file_path = ?"

LAST_INDEXED_AT_SQL = "SELECT value FROM meta WHERE key = 'last_indexed_at'"

UPSERT_LAST_INDEXED_AT_SQL = (
    "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_at', ?)"
)

DELETE_FILE_CACHE_SQL = "DELETE FROM file_cache"

SELECT_FILE_CACHE_SQL = "SELECT file_path, hash FROM file_cache"

UPSERT_NODE_SQL = """
    INSERT OR REPLACE INTO nodes
    (id, type, name, fqn, file_path, start_line, end_line,
     signature, return_type, docstring, is_exported, is_async,
     is_test, raw_body, skeleton_standard, skeleton_minimal, language,
     module, workspace_id, category)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_EDGE_IGNORE_SQL = (
    "INSERT OR IGNORE INTO edges (source_id, target_id, type, target_name, target_kind_hint, target_fqn_hint, resolution_status, resolution_confidence, call_site_line, confidence) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

UPSERT_FILE_CACHE_ENTRY_SQL = (
    "INSERT OR REPLACE INTO file_cache "
    "(file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)"
)

SELECT_NODES_ID_BY_PATH_SQL = "SELECT id FROM nodes WHERE file_path = ?"
DELETE_NODES_BY_PATH_SQL = "DELETE FROM nodes WHERE file_path = ?"
DELETE_FILE_CACHE_BY_PATH_SQL = "DELETE FROM file_cache WHERE file_path = ?"
SELECT_ALL_FILE_CACHE_PATHS_SQL = "SELECT file_path FROM file_cache"

DELETE_EDGES_BY_SOURCE_ID_SQL_TEMPLATE = "DELETE FROM edges WHERE source_id IN ({placeholders})"
DELETE_EDGES_BY_TARGET_ID_SQL_TEMPLATE = "DELETE FROM edges WHERE target_id IN ({placeholders})"

SELECT_NODE_ID_FQN_BY_NAME_SQL_TEMPLATE = "SELECT id, fqn FROM nodes WHERE name IN ({placeholders}) AND language = 'python'"
SELECT_EDGE_ID_LANG_BY_EDGE_ID_SQL_TEMPLATE = "SELECT e.id, n.language FROM edges e JOIN nodes n ON e.source_id = n.id WHERE e.id IN ({placeholders})"
SELECT_NODE_ID_NAME_BY_NAME_LANG_SQL_TEMPLATE = "SELECT id, name FROM nodes WHERE name IN ({placeholders}) AND language = ?"
SELECT_NODE_ID_NAME_BY_NAME_SQL_TEMPLATE = "SELECT id, name FROM nodes WHERE name IN ({placeholders})"

SELECT_UNRESOLVED_EDGES_SQL = "SELECT id, target_id, type, target_name, target_kind_hint, target_fqn_hint FROM edges WHERE resolution_status = 'unresolved' OR target_id LIKE '__unresolved%'"
UPDATE_EDGE_TARGET_ID_SQL = "UPDATE OR IGNORE edges SET target_id = ?, resolution_status = 'resolved' WHERE id = ?"
UPDATE_EDGE_STATUS_SQL = "UPDATE OR IGNORE edges SET resolution_status = ? WHERE id = ?"

SELECT_MEMORY_CONTENT_BY_KEY_SQL = "SELECT content FROM memories WHERE key = ?"
UPSERT_MEMORY_RULE_SQL = """INSERT INTO memories (key, project_id, category, content, tags, relationships, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
           content=excluded.content, category=excluded.category,
           tags=excluded.tags, updated_at=excluded.updated_at"""
SELECT_MEMORY_KEYS_BY_CATEGORY_TAG_SQL_TEMPLATE = "SELECT key FROM memories WHERE category IN ({placeholders}) AND tags LIKE '%agent-rule%'"
DELETE_MEMORIES_BY_KEYS_SQL_TEMPLATE = "DELETE FROM memories WHERE key IN ({placeholders})"

SELECT_NODE_ID_ROWID_BY_IDS_SQL_TEMPLATE = "SELECT id, rowid FROM nodes WHERE id IN ({placeholders})"
UPSERT_VEC_NODES_SQL = "INSERT OR REPLACE INTO vec_nodes (rowid, embedding) VALUES (?, ?)"


def delete_edges_by_source_id_sql(placeholders: str) -> str:
    return DELETE_EDGES_BY_SOURCE_ID_SQL_TEMPLATE.format(placeholders=placeholders)

def delete_edges_by_target_id_sql(placeholders: str) -> str:
    return DELETE_EDGES_BY_TARGET_ID_SQL_TEMPLATE.format(placeholders=placeholders)

def select_node_id_fqn_by_name_sql(placeholders: str) -> str:
    return SELECT_NODE_ID_FQN_BY_NAME_SQL_TEMPLATE.format(placeholders=placeholders)

def select_edge_id_lang_by_edge_id_sql(placeholders: str) -> str:
    return SELECT_EDGE_ID_LANG_BY_EDGE_ID_SQL_TEMPLATE.format(placeholders=placeholders)

def select_node_id_name_by_name_lang_sql(placeholders: str) -> str:
    return SELECT_NODE_ID_NAME_BY_NAME_LANG_SQL_TEMPLATE.format(placeholders=placeholders)

def select_node_id_name_by_name_sql(placeholders: str) -> str:
    return SELECT_NODE_ID_NAME_BY_NAME_SQL_TEMPLATE.format(placeholders=placeholders)

def select_memory_keys_by_category_tag_sql(placeholders: str) -> str:
    return SELECT_MEMORY_KEYS_BY_CATEGORY_TAG_SQL_TEMPLATE.format(placeholders=placeholders)

def delete_memories_by_keys_sql(placeholders: str) -> str:
    return DELETE_MEMORIES_BY_KEYS_SQL_TEMPLATE.format(placeholders=placeholders)

def select_node_id_rowid_by_ids_sql(placeholders: str) -> str:
    return SELECT_NODE_ID_ROWID_BY_IDS_SQL_TEMPLATE.format(placeholders=placeholders)
