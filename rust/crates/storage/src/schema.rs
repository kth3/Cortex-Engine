use rusqlite::{Connection, Result};

const SCHEMA_VERSION_META_KEY: &str = "schema_version";
const CURRENT_SCHEMA_VERSION: &str = "2";
const INIT_SCHEMA_VERSION_SQL: &str = "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)";
const UPGRADE_SCHEMA_VERSION_SQL: &str =
    "UPDATE meta SET value = ? WHERE key = 'schema_version' AND value < ?";

const CREATE_CORE_TABLES_SQL: &str = r#"
    CREATE TABLE IF NOT EXISTS file_cache (
        file_path   TEXT PRIMARY KEY,
        hash        TEXT NOT NULL,
        last_indexed_at INTEGER NOT NULL,
        node_count  INTEGER DEFAULT 0,
        workspace_id TEXT DEFAULT 'default'
    );

    CREATE TABLE IF NOT EXISTS nodes (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        name        TEXT NOT NULL,
        fqn         TEXT NOT NULL,
        file_path   TEXT NOT NULL,
        start_line  INTEGER NOT NULL,
        end_line    INTEGER NOT NULL,
        signature   TEXT,
        return_type TEXT,
        docstring   TEXT,
        is_exported INTEGER DEFAULT 1,
        is_async    INTEGER DEFAULT 0,
        is_test     INTEGER DEFAULT 0,
        raw_body    TEXT,
        skeleton_standard TEXT,
        skeleton_minimal  TEXT,
        language    TEXT NOT NULL,
        module      TEXT DEFAULT 'unknown',
        workspace_id TEXT DEFAULT 'default',
        category    TEXT DEFAULT 'SOURCE'
    );

    CREATE TABLE IF NOT EXISTS edges (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id   TEXT NOT NULL,
        target_id   TEXT NOT NULL,
        type        TEXT NOT NULL DEFAULT 'CALLS',
        target_name TEXT,
        target_kind_hint TEXT,
        target_fqn_hint TEXT,
        resolution_status TEXT DEFAULT 'unresolved',
        resolution_confidence REAL DEFAULT 1.0,
        call_site_line INTEGER,
        confidence  REAL DEFAULT 1.0,
        UNIQUE(source_id, target_id, type)
    );
    CREATE INDEX IF NOT EXISTS idx_edges_hint_name ON edges(target_name);
    CREATE INDEX IF NOT EXISTS idx_edges_hint_kind ON edges(target_kind_hint);
"#;

const CREATE_HISTORY_TABLES_SQL: &str = r#"
    CREATE TABLE IF NOT EXISTS file_lineage (
        file_path       TEXT PRIMARY KEY,
        commit_count    INTEGER DEFAULT 0,
        churn_score     REAL DEFAULT 0.0,
        last_author     TEXT DEFAULT '',
        last_commit_ts  INTEGER DEFAULT 0,
        updated_at      INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS co_change_edges (
        file_a          TEXT NOT NULL,
        file_b          TEXT NOT NULL,
        coupling_score  REAL NOT NULL,
        shared_commits  INTEGER NOT NULL,
        updated_at      INTEGER NOT NULL,
        PRIMARY KEY (file_a, file_b)
    );

    CREATE TABLE IF NOT EXISTS ast_diffs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path       TEXT NOT NULL,
        symbol_fqn      TEXT NOT NULL,
        diff_type       TEXT NOT NULL,
        summary         TEXT NOT NULL,
        old_snippet     TEXT,
        new_snippet     TEXT,
        detected_at     INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS file_edit_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path     TEXT NOT NULL,
        before_hash   TEXT NOT NULL,
        after_hash    TEXT NOT NULL,
        line_range    TEXT,
        tool_name     TEXT,
        event_sources TEXT NOT NULL,
        session_id    TEXT NOT NULL,
        edit_summary  TEXT,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        UNIQUE(file_path, before_hash, after_hash, session_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fee_path_updated
        ON file_edit_events(file_path, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_fee_session_updated
        ON file_edit_events(session_id, updated_at DESC);
"#;

const CREATE_MEMORY_TABLES_SQL: &str = r#"
    CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        agent_name      TEXT DEFAULT 'unknown',
        started_at      INTEGER,
        last_active_at  INTEGER,
        status          TEXT DEFAULT 'active',
        summary         TEXT,
        tool_call_count INTEGER DEFAULT 0,
        observation_count INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS observations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT,
        type            TEXT NOT NULL,
        content         TEXT NOT NULL,
        file_paths      TEXT,
        stale           INTEGER DEFAULT 0,
        created_at      INTEGER NOT NULL,
        source          TEXT DEFAULT 'agent',
        confidence      REAL DEFAULT 1.0,
        category        TEXT
    );

    CREATE TABLE IF NOT EXISTS memories (
        key         TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        category    TEXT NOT NULL,
        content     TEXT NOT NULL,
        tags        TEXT,
        relationships TEXT,
        access_count INTEGER DEFAULT 0,
        created_at  INTEGER NOT NULL,
        updated_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS search_misses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        query       TEXT NOT NULL,
        project_id  TEXT,
        category    TEXT,
        created_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS meta (
        key     TEXT PRIMARY KEY,
        value   TEXT
    );
"#;

const CREATE_FTS_AND_TRIGGERS_SQL: &str = r#"
    CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
        name, fqn, docstring, signature,
        content='nodes',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
        INSERT INTO nodes_fts(rowid, name, fqn, docstring, signature)
        VALUES (new.rowid, new.name, new.fqn, new.docstring, new.signature);
    END;

    CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, name, fqn, docstring, signature)
        VALUES ('delete', old.rowid, old.name, old.fqn, old.docstring, old.signature);
    END;

    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        key, content, tags, category,
        content='memories',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, key, content, tags, category)
        VALUES (new.rowid, new.key, new.content, new.tags, new.category);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, key, content, tags, category)
        VALUES ('delete', old.rowid, old.key, old.content, old.tags, old.category);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, key, content, tags, category)
        VALUES ('delete', old.rowid, old.key, old.content, old.tags, old.category);
        INSERT INTO memories_fts(rowid, key, content, tags, category)
        VALUES (new.rowid, new.key, new.content, new.tags, new.category);
    END;
"#;

const CREATE_INDEXES_SQL: &str = r#"
    CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
    CREATE INDEX IF NOT EXISTS idx_nodes_fqn ON nodes(fqn);
    CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
    CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
    CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
    CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
    CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);
    CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
    CREATE INDEX IF NOT EXISTS idx_search_misses_ts ON search_misses(created_at);
"#;

pub fn init_schema(conn: &Connection) -> Result<()> {
    conn.execute_batch(CREATE_CORE_TABLES_SQL)?;
    conn.execute_batch(CREATE_HISTORY_TABLES_SQL)?;
    conn.execute_batch(CREATE_MEMORY_TABLES_SQL)?;
    apply_legacy_migrations(conn)?;
    conn.execute_batch(CREATE_FTS_AND_TRIGGERS_SQL)?;
    conn.execute_batch(CREATE_INDEXES_SQL)?;
    initialize_meta(conn)?;
    Ok(())
}

fn apply_legacy_migrations(conn: &Connection) -> Result<()> {
    add_column_if_missing(conn, "nodes", "module", "TEXT DEFAULT 'unknown'")?;
    add_column_if_missing(conn, "nodes", "workspace_id", "TEXT DEFAULT 'default'")?;
    add_column_if_missing(conn, "nodes", "category", "TEXT DEFAULT 'SOURCE'")?;

    add_column_if_missing(conn, "file_cache", "workspace_id", "TEXT DEFAULT 'default'")?;

    add_column_if_missing(conn, "edges", "target_name", "TEXT")?;
    add_column_if_missing(conn, "edges", "target_kind_hint", "TEXT")?;
    add_column_if_missing(conn, "edges", "target_fqn_hint", "TEXT")?;
    add_column_if_missing(conn, "edges", "resolution_status", "TEXT DEFAULT 'unresolved'")?;
    add_column_if_missing(conn, "edges", "resolution_confidence", "REAL DEFAULT 1.0")?;

    Ok(())
}

fn add_column_if_missing(conn: &Connection, table: &str, column: &str, ddl: &str) -> Result<()> {
    if !has_column(conn, table, column)? {
        let sql = format!("ALTER TABLE {} ADD COLUMN {} {}", table, column, ddl);
        conn.execute(&sql, [])?;
    }
    Ok(())
}

fn has_column(conn: &Connection, table: &str, column: &str) -> Result<bool> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({})", table))?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(columns.iter().any(|name| name == column))
}

fn initialize_meta(conn: &Connection) -> Result<()> {
    conn.execute(
        INIT_SCHEMA_VERSION_SQL,
        (SCHEMA_VERSION_META_KEY, CURRENT_SCHEMA_VERSION),
    )?;
    conn.execute(
        UPGRADE_SCHEMA_VERSION_SQL,
        (CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION),
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::init_schema;
    use rusqlite::Connection;

    #[test]
    fn init_schema_creates_core_tables_and_version() {
        let conn = Connection::open_in_memory().expect("open in-memory db");
        init_schema(&conn).expect("init schema");

        let version: String = conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_version'",
                [],
                |row| row.get(0),
            )
            .expect("schema_version");
        assert_eq!(version, "2");

        for table in ["nodes", "edges", "file_cache", "meta", "nodes_fts"] {
            let exists: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?1",
                    [table],
                    |row| row.get(0),
                )
                .expect("sqlite_master lookup");
            assert_eq!(exists, 1, "missing table {}", table);
        }
    }
}
