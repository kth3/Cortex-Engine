"""SQL constants for node and edge storage operations."""

SEARCH_NODES_FTS_WITH_CATEGORY_SQL = """SELECT n.* FROM nodes_fts f
                   JOIN nodes n ON n.rowid = f.rowid
                   WHERE nodes_fts MATCH ? AND n.category = ?
                   ORDER BY rank
                   LIMIT ?"""

SEARCH_NODES_FTS_SQL = """SELECT n.* FROM nodes_fts f
                   JOIN nodes n ON n.rowid = f.rowid
                   WHERE nodes_fts MATCH ?
                   ORDER BY CASE WHEN n.category = 'SOURCE' THEN 0 ELSE 1 END, rank
                   LIMIT ?"""

SELECT_NODE_BY_FQN_SQL = "SELECT * FROM nodes WHERE fqn = ?"
SELECT_NODE_BY_ID_SQL = "SELECT * FROM nodes WHERE id = ?"

SELECT_CALLERS_SQL = """SELECT n.*, e.type as edge_type, e.call_site_line
           FROM edges e JOIN nodes n ON n.id = e.source_id
           WHERE e.target_id = ?
              OR e.target_id = '__unresolved__::' || (SELECT name FROM nodes WHERE id = ?)"""

SELECT_CALLEES_SQL = """SELECT DISTINCT n.*, e.type as edge_type, e.call_site_line
           FROM edges e JOIN nodes n
             ON (n.id = e.target_id
                 OR e.target_id = '__unresolved__::' || n.name)
           WHERE e.source_id = ?"""

COUNT_NODES_SQL = "SELECT COUNT(*) FROM nodes"
COUNT_EDGES_SQL = "SELECT COUNT(*) FROM edges"
COUNT_FILES_SQL = "SELECT COUNT(*) FROM file_cache"
COUNT_MEMORIES_SQL = "SELECT COUNT(*) FROM memories"
SELECT_SCHEMA_VERSION_SQL = "SELECT value FROM meta WHERE key='schema_version'"
