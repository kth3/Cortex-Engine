//! SQLite writer for Cortex indexing output.
//!
//! This crate keeps the write path small and explicit so the watcher can call it
//! later without depending on Python-side helpers.

use std::path::Path;

use cortex_parsers::{EdgeRecord, NodeRecord, UNRESOLVED_FQN_PREFIX};
use rusqlite::{params, Connection, Result, Transaction};

/// Default workspace id used when the caller does not provide one.
pub const DEFAULT_WORKSPACE_ID: &str = "default";

/// Default node module used when the caller does not provide one.
pub const DEFAULT_NODE_MODULE: &str = "unknown";

/// Default node category used when the caller does not provide one.
pub const DEFAULT_NODE_CATEGORY: &str = "SOURCE";

const SQLITE_BUSY_TIMEOUT_MS: i64 = 5000;
const SQLITE_CACHE_SIZE: i64 = -2000;

const INSERT_NODE_SQL: &str = r#"
    INSERT OR REPLACE INTO nodes (
        id, type, name, fqn, file_path, start_line, end_line,
        signature, return_type, docstring, is_exported, is_async,
        is_test, raw_body, skeleton_standard, skeleton_minimal, language,
        module, workspace_id, category
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        COALESCE(?, 'unknown'),
        COALESCE(?, 'default'),
        COALESCE(?, 'SOURCE')
    )
"#;

const INSERT_EDGE_SQL: &str = r#"
    INSERT OR IGNORE INTO edges (
        source_id, target_id, type, target_name, target_kind_hint,
        target_fqn_hint, resolution_status, resolution_confidence,
        call_site_line, confidence
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"#;

const UPSERT_FILE_CACHE_SQL: &str = r#"
    INSERT OR REPLACE INTO file_cache (
        file_path, hash, last_indexed_at, workspace_id
    ) VALUES (?, ?, ?, COALESCE(?, 'default'))
"#;

/// Re-export parser records so the storage API stays self-contained.
pub use cortex_parsers::{ParseResult, UNRESOLVED_NAME_PREFIX};

/// Open a SQLite database and apply the pragmas used by the Python storage layer.
pub fn open_connection(path: impl AsRef<Path>) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch(&format!(
        "PRAGMA journal_mode=WAL;\
         PRAGMA busy_timeout={};\
         PRAGMA foreign_keys=ON;\
         PRAGMA cache_size={};",
        SQLITE_BUSY_TIMEOUT_MS, SQLITE_CACHE_SIZE
    ))?;
    Ok(conn)
}

/// Derive the edge resolution status from the target id prefix.
pub fn resolution_status_for_target(target_id: &str) -> &'static str {
    if target_id.starts_with(UNRESOLVED_FQN_PREFIX) || target_id.starts_with(UNRESOLVED_NAME_PREFIX)
    {
        "unresolved"
    } else {
        "resolved"
    }
}

/// File-level write payload.
///
/// `module`, `workspace_id`, and `category` are optional because the Rust parser
/// records do not carry them. When omitted, the database defaults are used.
pub struct FileWriteBatch<'a> {
    pub file_path: &'a str,
    pub file_hash: &'a str,
    pub indexed_at: i64,
    pub module: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
    pub category: Option<&'a str>,
    pub nodes: &'a [NodeRecord],
    pub edges: &'a [EdgeRecord],
}

/// Persist one parsed file in a single transaction.
pub fn write_file_batch(conn: &mut Connection, batch: &FileWriteBatch<'_>) -> Result<()> {
    let tx = conn.transaction()?;
    insert_nodes(&tx, batch.nodes, batch.module, batch.workspace_id, batch.category)?;
    insert_edges(&tx, batch.edges)?;
    upsert_file_cache(
        &tx,
        batch.file_path,
        batch.file_hash,
        batch.indexed_at,
        batch.workspace_id,
    )?;
    tx.commit()
}

/// Persist nodes with `INSERT OR REPLACE`.
pub fn insert_nodes(
    tx: &Transaction<'_>,
    nodes: &[NodeRecord],
    module: Option<&str>,
    workspace_id: Option<&str>,
    category: Option<&str>,
) -> Result<()> {
    if nodes.is_empty() {
        return Ok(());
    }

    let mut stmt = tx.prepare_cached(INSERT_NODE_SQL)?;
    for node in nodes {
        stmt.execute(params![
            node.id.as_str(),
            node.node_type.as_str(),
            node.name.as_str(),
            node.fqn.as_str(),
            node.file_path.as_str(),
            i64::from(node.start_line),
            i64::from(node.end_line),
            node.signature.as_deref(),
            node.return_type.as_deref(),
            node.docstring.as_deref(),
            node.is_exported.map(i64::from),
            node.is_async.map(i64::from),
            node.is_test.map(i64::from),
            node.raw_body.as_str(),
            node.skeleton_standard.as_deref(),
            node.skeleton_minimal.as_deref(),
            node.language.as_str(),
            module,
            workspace_id,
            category,
        ])?;
    }

    Ok(())
}

/// Persist edges with `INSERT OR IGNORE`.
pub fn insert_edges(tx: &Transaction<'_>, edges: &[EdgeRecord]) -> Result<()> {
    if edges.is_empty() {
        return Ok(());
    }

    let mut stmt = tx.prepare_cached(INSERT_EDGE_SQL)?;
    for edge in edges {
        stmt.execute(params![
            edge.source_id.as_str(),
            edge.target_id.as_str(),
            edge.edge_type.as_str(),
            edge.target_name.as_deref(),
            edge.target_kind_hint.as_deref(),
            edge.target_fqn_hint.as_deref(),
            resolution_status_for_target(&edge.target_id),
            1.0_f64,
            edge.call_site_line.map(i64::from),
            edge.confidence,
        ])?;
    }

    Ok(())
}

/// Upsert the file cache row for a parsed file.
pub fn upsert_file_cache(
    tx: &Transaction<'_>,
    file_path: &str,
    file_hash: &str,
    indexed_at: i64,
    workspace_id: Option<&str>,
) -> Result<()> {
    tx.execute(
        UPSERT_FILE_CACHE_SQL,
        params![file_path, file_hash, indexed_at, workspace_id],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::fs;
    use std::path::PathBuf;

    static NEXT_DB_ID: AtomicU64 = AtomicU64::new(0);

    fn temp_db_path(name: &str) -> PathBuf {
        let n = NEXT_DB_ID.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "cortex_storage_{}_{}_{}.db",
            name,
            std::process::id(),
            n
        ))
    }

    fn create_schema(conn: &Connection) -> Result<()> {
        conn.execute_batch(
            r#"
            CREATE TABLE file_cache (
                file_path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                last_indexed_at INTEGER NOT NULL,
                node_count INTEGER DEFAULT 0,
                workspace_id TEXT DEFAULT 'default'
            );

            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                fqn TEXT NOT NULL,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT,
                return_type TEXT,
                docstring TEXT,
                is_exported INTEGER DEFAULT 1,
                is_async INTEGER DEFAULT 0,
                is_test INTEGER DEFAULT 0,
                raw_body TEXT,
                skeleton_standard TEXT,
                skeleton_minimal TEXT,
                language TEXT NOT NULL,
                module TEXT DEFAULT 'unknown',
                workspace_id TEXT DEFAULT 'default',
                category TEXT DEFAULT 'SOURCE'
            );

            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'CALLS',
                target_name TEXT,
                target_kind_hint TEXT,
                target_fqn_hint TEXT,
                resolution_status TEXT DEFAULT 'unresolved',
                resolution_confidence REAL DEFAULT 1.0,
                call_site_line INTEGER,
                confidence REAL DEFAULT 1.0,
                UNIQUE(source_id, target_id, type)
            );
            "#,
        )
    }

    fn sample_node() -> NodeRecord {
        NodeRecord {
            id: "node-1".to_string(),
            node_type: "FUNCTION".to_string(),
            name: "sample".to_string(),
            fqn: "sample::node".to_string(),
            file_path: "src/lib.rs".to_string(),
            start_line: 1,
            end_line: 4,
            signature: Some("fn sample()".to_string()),
            return_type: None,
            docstring: Some("demo".to_string()),
            is_exported: Some(1),
            is_async: Some(0),
            is_test: Some(0),
            raw_body: "fn sample() {}".to_string(),
            skeleton_standard: None,
            skeleton_minimal: None,
            language: "rust".to_string(),
        }
    }

    fn sample_edge(target_id: &str) -> EdgeRecord {
        EdgeRecord {
            source_id: "node-1".to_string(),
            target_id: target_id.to_string(),
            edge_type: "CALLS".to_string(),
            target_name: Some("callee".to_string()),
            target_kind_hint: Some("FUNCTION".to_string()),
            target_fqn_hint: Some("sample::callee".to_string()),
            call_site_line: Some(9),
            confidence: 0.75,
        }
    }

    #[test]
    fn writes_node_and_file_cache_row() -> Result<()> {
        let db_path = temp_db_path("node_cache");
        let mut conn = open_connection(&db_path)?;
        create_schema(&conn)?;

        let node = sample_node();
        let batch = FileWriteBatch {
            file_path: "src/lib.rs",
            file_hash: "hash-1",
            indexed_at: 123,
            module: None,
            workspace_id: None,
            category: None,
            nodes: std::slice::from_ref(&node),
            edges: &[],
        };

        write_file_batch(&mut conn, &batch)?;

        let row: (String, String, String) = conn.query_row(
            "SELECT module, workspace_id, category FROM nodes WHERE id = ?1",
            params![node.id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        assert_eq!(row.0, DEFAULT_NODE_MODULE);
        assert_eq!(row.1, DEFAULT_WORKSPACE_ID);
        assert_eq!(row.2, DEFAULT_NODE_CATEGORY);

        let cache_row: (String, i64, String) = conn.query_row(
            "SELECT hash, last_indexed_at, workspace_id FROM file_cache WHERE file_path = ?1",
            params!["src/lib.rs"],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        assert_eq!(cache_row.0, "hash-1");
        assert_eq!(cache_row.1, 123);
        assert_eq!(cache_row.2, DEFAULT_WORKSPACE_ID);

        fs::remove_file(db_path).ok();
        Ok(())
    }

    #[test]
    fn unresolved_edge_gets_unresolved_status() -> Result<()> {
        let db_path = temp_db_path("unresolved_edge");
        let mut conn = open_connection(&db_path)?;
        create_schema(&conn)?;

        let edge = sample_edge("__unresolved_fqn__::sample::callee");
        let batch = FileWriteBatch {
            file_path: "src/lib.rs",
            file_hash: "hash-2",
            indexed_at: 456,
            module: None,
            workspace_id: None,
            category: None,
            nodes: &[],
            edges: std::slice::from_ref(&edge),
        };

        write_file_batch(&mut conn, &batch)?;

        let status: String = conn.query_row(
            "SELECT resolution_status FROM edges WHERE source_id = ?1",
            params![edge.source_id.as_str()],
            |row| row.get(0),
        )?;
        assert_eq!(status, "unresolved");

        fs::remove_file(db_path).ok();
        Ok(())
    }

    #[test]
    fn resolved_edge_gets_resolved_status() -> Result<()> {
        let db_path = temp_db_path("resolved_edge");
        let mut conn = open_connection(&db_path)?;
        create_schema(&conn)?;

        let edge = sample_edge("sample::callee");
        let batch = FileWriteBatch {
            file_path: "src/lib.rs",
            file_hash: "hash-3",
            indexed_at: 789,
            module: None,
            workspace_id: None,
            category: None,
            nodes: &[],
            edges: std::slice::from_ref(&edge),
        };

        write_file_batch(&mut conn, &batch)?;

        let status: String = conn.query_row(
            "SELECT resolution_status FROM edges WHERE source_id = ?1",
            params![edge.source_id.as_str()],
            |row| row.get(0),
        )?;
        assert_eq!(status, "resolved");

        fs::remove_file(db_path).ok();
        Ok(())
    }
}
