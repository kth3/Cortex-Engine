"""indexing 계층에서 사용하는 SQL 문자열 모음.

retrieval/queries.py가 검색 쿼리 전용인 것처럼, 이 모듈은 indexer와
indexing records 계층이 file_cache, meta, nodes, edges 상태를 읽고
갱신할 때 사용하는 SQL만 관리한다.
SQL 의미, 반환 컬럼 순서, placeholder 개수는 호출부 계약이므로 변경하지 않는다.
"""

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
    "INSERT OR IGNORE INTO edges (source_id, target_id, type) VALUES (?, ?, ?)"
)

UPSERT_FILE_CACHE_ENTRY_SQL = (
    "INSERT OR REPLACE INTO file_cache "
    "(file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)"
)
