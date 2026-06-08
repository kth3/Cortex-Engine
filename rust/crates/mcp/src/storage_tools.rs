use std::collections::{HashMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use cortex_parsers::{
    parse_c_file, parse_csharp_file, parse_css_file, parse_html_file, parse_java_file,
    parse_markdown_file, parse_pdf_file, parse_python_file, parse_ts_file, ParseResult,
};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

pub type ToolResult = Result<Value, String>;

#[path = "edit.rs"]
mod edit;
#[path = "embedding.rs"]
mod embedding;
#[path = "memory.rs"]
mod memory;
#[path = "query.rs"]
mod query;

pub use edit::call_replace_exact_text;
pub(crate) use memory::save_observation;
pub(crate) use memory::search_memories_fts;
pub use memory::{
    call_consolidate_memory, call_create_task_contract, call_manage_todo, call_read_memory,
    call_save_observation, call_search_memory, call_sync_session_memory, call_write_memory,
};
pub(crate) use query::snippet;
pub use query::{
    call_find_execution_path, call_get_file_git_history, call_get_file_outline,
    call_get_impact_graph, call_get_index_status, call_get_session_context,
    call_read_file_with_hash, call_resolve_symbol, call_search_context, call_search_deep_context,
};

const DEFAULT_RESOLVE_LIMIT: usize = 5;
const DEFAULT_SEARCH_TOKEN_BUDGET: usize = 4000;
const DEFAULT_DEEP_CONTEXT_LIMIT: usize = 5;
const FTS_PROBE_MULTIPLIER: usize = 3;
const DEFAULT_IMPACT_DIRECTION: &str = "both";
const DEFAULT_IMPACT_MAX_DEPTH: u32 = 2;
const DEFAULT_IMPACT_MAX_NODES: u32 = 50;
const DEFAULT_LOGIC_MAX_DEPTH: u32 = 6;
const DEFAULT_LOGIC_MAX_NODES: u32 = 200;
const DEFAULT_SESSION_TOKEN_BUDGET: u32 = 2000;
const WORKSPACES_DIRNAME: &str = "workspaces";
const GLOBAL_DATA_DIRNAME: &str = "data";
const MEMORY_DB_FILENAME: &str = "memories.db";
const GRAPH_STORE_DIRNAME: &str = "graph_db_store";
const BOARD_STATE_DIRNAME: &str = "state";
const BOARD_JSON_FILENAME: &str = "board.json";
const SESSION_ID: &str = "rust-mcp";
const GLOBAL_WORKSPACE_ID: &str = "global";
const GLOBAL_PROJECT_ID: &str = "global";
const GLOBAL_MEMORY_KEY_PREFIX: &str = "global::";
const GLOBAL_DOC_EXTENSIONS: &[&str] = &["md", "markdown", "txt", "json", "yaml", "yml"];

#[derive(Debug, Clone)]
pub(crate) struct Node {
    id: String,
    node_type: String,
    name: String,
    fqn: String,
    file_path: Option<String>,
    start_line: Option<i64>,
    language: Option<String>,
}

fn absolute_path(path: impl AsRef<Path>) -> PathBuf {
    let path = path.as_ref();
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

fn home_dir() -> PathBuf {
    if let Some(home) = env::var_os("HOME") {
        return PathBuf::from(home);
    }
    if let Some(profile) = env::var_os("USERPROFILE") {
        return PathBuf::from(profile);
    }
    match (env::var_os("HOMEDRIVE"), env::var_os("HOMEPATH")) {
        (Some(drive), Some(path)) => {
            let mut home = PathBuf::from(drive);
            home.push(path);
            home
        }
        _ => env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    }
}

fn data_home() -> PathBuf {
    env::var_os("CORTEX_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".cortex"))
}

fn global_data_home() -> PathBuf {
    data_home().join(GLOBAL_DATA_DIRNAME)
}

fn sha1_hex(input: &[u8]) -> String {
    fn left_rotate(value: u32, bits: u32) -> u32 {
        (value << bits) | (value >> (32 - bits))
    }

    let mut data = input.to_vec();
    let bit_len = (data.len() as u64) * 8;
    data.push(0x80);
    while (data.len() % 64) != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());

    let mut h0: u32 = 0x67452301;
    let mut h1: u32 = 0xEFCDAB89;
    let mut h2: u32 = 0x98BADCFE;
    let mut h3: u32 = 0x10325476;
    let mut h4: u32 = 0xC3D2E1F0;

    for chunk in data.chunks_exact(64) {
        let mut w = [0u32; 80];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            let idx = i * 4;
            *word =
                u32::from_be_bytes([chunk[idx], chunk[idx + 1], chunk[idx + 2], chunk[idx + 3]]);
        }
        for i in 16..80 {
            w[i] = left_rotate(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
        }

        let mut a = h0;
        let mut b = h1;
        let mut c = h2;
        let mut d = h3;
        let mut e = h4;

        for i in 0..80 {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A827999),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let temp = left_rotate(a, 5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(w[i]);
            e = d;
            d = c;
            c = left_rotate(b, 30);
            b = a;
            a = temp;
        }

        h0 = h0.wrapping_add(a);
        h1 = h1.wrapping_add(b);
        h2 = h2.wrapping_add(c);
        h3 = h3.wrapping_add(d);
        h4 = h4.wrapping_add(e);
    }

    let mut out = String::with_capacity(40);
    for word in [h0, h1, h2, h3, h4] {
        out.push_str(&format!("{word:08x}"));
    }
    out
}

fn workspace_key(workspace: impl AsRef<Path>) -> String {
    if let Some(key) = env::var_os("CORTEX_WORKSPACE_KEY") {
        return key.to_string_lossy().into_owned();
    }
    let workspace = absolute_path(workspace);
    sha1_hex(workspace.to_string_lossy().as_bytes())
        .chars()
        .take(12)
        .collect()
}

fn workspace_data_dir(workspace: impl AsRef<Path>) -> PathBuf {
    data_home()
        .join(WORKSPACES_DIRNAME)
        .join(workspace_key(workspace))
}

fn memories_db_path(workspace: impl AsRef<Path>) -> PathBuf {
    workspace_data_dir(workspace).join(MEMORY_DB_FILENAME)
}

pub(crate) fn graph_store_path(workspace: impl AsRef<Path>) -> PathBuf {
    workspace_data_dir(workspace).join(GRAPH_STORE_DIRNAME)
}

fn global_memories_db_path() -> PathBuf {
    global_data_home().join(MEMORY_DB_FILENAME)
}

fn board_json_path(workspace: impl AsRef<Path>) -> PathBuf {
    workspace_data_dir(workspace)
        .join(BOARD_STATE_DIRNAME)
        .join(BOARD_JSON_FILENAME)
}

pub(crate) fn workspace_history_dir(workspace: impl AsRef<Path>) -> PathBuf {
    cortex_root(workspace).join("history")
}

fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn now_text() -> String {
    now_unix().to_string()
}

fn cortex_root(workspace: impl AsRef<Path>) -> PathBuf {
    let workspace = absolute_path(workspace);
    if workspace.file_name().and_then(|name| name.to_str()) == Some(".cortex") {
        workspace
    } else {
        workspace.join(".cortex")
    }
}

pub(crate) fn open_connection(workspace: impl AsRef<Path>) -> Result<Connection, String> {
    let path = memories_db_path(workspace);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let conn = cortex_storage::open_connection(&path).map_err(|err| err.to_string())?;
    cortex_storage::init_schema(&conn).map_err(|err| err.to_string())?;
    Ok(conn)
}

fn open_global_connection(workspace: impl AsRef<Path>) -> Result<Connection, String> {
    let path = global_memories_db_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let conn = cortex_storage::open_connection(&path).map_err(|err| err.to_string())?;
    cortex_storage::init_schema(&conn).map_err(|err| err.to_string())?;
    sync_global_knowledge(&conn, workspace.as_ref())?;
    Ok(conn)
}

fn sync_global_knowledge(conn: &Connection, workspace: &Path) -> Result<(), String> {
    let root = cortex_home_root(workspace);
    let mut seen = HashSet::new();

    for subdir in ["rules", "knowledge"] {
        let dir = root.join(subdir);
        if !dir.exists() {
            continue;
        }
        for entry in WalkDir::new(&dir)
            .into_iter()
            .filter_map(|entry| entry.ok())
        {
            if !entry.file_type().is_file() {
                continue;
            }
            let path = entry.path();
            if !is_global_doc(path) {
                continue;
            }
            let rel_path = match path.strip_prefix(&root) {
                Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
                Err(_) => continue,
            };
            let key = format!("{GLOBAL_MEMORY_KEY_PREFIX}{rel_path}");
            let content = fs::read_to_string(path)
                .map(|text| text.replace("\r\n", "\n"))
                .map_err(|err| err.to_string())?;
            let hash = sha256_hex(content.as_bytes());
            seen.insert(rel_path.clone());

            let existing_hash = conn
                .query_row(
                    "SELECT hash FROM file_cache WHERE file_path = ?1 AND workspace_id = ?2",
                    params![rel_path, GLOBAL_WORKSPACE_ID],
                    |row| row.get::<_, String>(0),
                )
                .optional()
                .map_err(|err| err.to_string())?;
            if existing_hash.as_deref() == Some(hash.as_str()) {
                continue;
            }

            upsert_memory_record(
                conn,
                &key,
                GLOBAL_PROJECT_ID,
                global_doc_category(&rel_path),
                &content,
                None,
                None,
            )?;
            conn.execute(
                "INSERT OR REPLACE INTO file_cache (file_path, hash, last_indexed_at, node_count, workspace_id)
                 VALUES (?1, ?2, ?3, 0, ?4)",
                params![rel_path, hash, now_unix(), GLOBAL_WORKSPACE_ID],
            )
            .map_err(|err| err.to_string())?;
        }
    }

    let mut stmt = conn
        .prepare("SELECT file_path FROM file_cache WHERE workspace_id = ?1")
        .map_err(|err| err.to_string())?;
    let stale_paths = stmt
        .query_map(params![GLOBAL_WORKSPACE_ID], |row| row.get::<_, String>(0))
        .map_err(|err| err.to_string())?
        .filter_map(|row| row.ok())
        .filter(|rel_path| !seen.contains(rel_path))
        .collect::<Vec<_>>();

    for rel_path in stale_paths {
        let key = format!("{GLOBAL_MEMORY_KEY_PREFIX}{rel_path}");
        conn.execute("DELETE FROM memories WHERE key = ?1", params![key])
            .map_err(|err| err.to_string())?;
        conn.execute(
            "DELETE FROM file_cache WHERE file_path = ?1 AND workspace_id = ?2",
            params![rel_path, GLOBAL_WORKSPACE_ID],
        )
        .map_err(|err| err.to_string())?;
    }

    Ok(())
}

fn cortex_home_root(workspace: impl AsRef<Path>) -> PathBuf {
    if let Some(home) = env::var_os("CORTEX_HOME") {
        let home = PathBuf::from(home);
        if home.exists() {
            return home;
        }
    }

    let workspace = absolute_path(workspace);
    if workspace.file_name().and_then(|name| name.to_str()) == Some(".cortex") {
        return workspace;
    }

    let nested = workspace.join(".cortex");
    if nested.exists() {
        return nested;
    }

    workspace
}

fn is_global_doc(path: &Path) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .map(|ext| {
            let ext = ext.to_ascii_lowercase();
            GLOBAL_DOC_EXTENSIONS
                .iter()
                .any(|candidate| candidate == &ext.as_str())
        })
        .unwrap_or(false)
}

fn global_doc_category(rel_path: &str) -> &'static str {
    let normalized = rel_path.replace('\\', "/");
    if normalized.starts_with("rules/") {
        "rule"
    } else if normalized.contains("/skills/") {
        "skill"
    } else if normalized.contains("protocol") {
        "protocol"
    } else {
        "knowledge"
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn upsert_memory_record(
    conn: &Connection,
    key: &str,
    project_id: &str,
    category: &str,
    content: &str,
    tags: Option<Value>,
    relationships: Option<Value>,
) -> Result<(), String> {
    if key.is_empty() {
        return Err("memory key is required".to_string());
    }
    let now = now_unix();
    let tags =
        serde_json::to_string(&tags.unwrap_or_else(|| json!([]))).map_err(|err| err.to_string())?;
    let relationships = serde_json::to_string(&relationships.unwrap_or_else(|| json!({})))
        .map_err(|err| err.to_string())?;
    let exists = conn
        .query_row(
            "SELECT key FROM memories WHERE key = ?1",
            params![key],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|err| err.to_string())?
        .is_some();
    if exists {
        conn.execute(
            "UPDATE memories
             SET project_id = ?1, category = ?2, content = ?3, tags = ?4, relationships = ?5,
                 updated_at = ?6, access_count = access_count + 1
             WHERE key = ?7",
            params![project_id, category, content, tags, relationships, now, key],
        )
        .map_err(|err| err.to_string())?;
    } else {
        conn.execute(
            "INSERT INTO memories
             (key, project_id, category, content, tags, relationships, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                key,
                project_id,
                category,
                content,
                tags,
                relationships,
                now,
                now
            ],
        )
        .map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn count_table(conn: &Connection, table: &str) -> Result<i64, String> {
    conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
        row.get(0)
    })
    .map_err(|err| err.to_string())
}

fn node_from_row(row: &Row<'_>) -> rusqlite::Result<Node> {
    Ok(Node {
        id: row.get("id")?,
        node_type: row.get("type")?,
        name: row.get("name")?,
        fqn: row.get("fqn")?,
        file_path: row.get("file_path")?,
        start_line: row.get("start_line")?,
        language: row.get("language")?,
    })
}

fn get_node_by_fqn(conn: &Connection, fqn: &str) -> Result<Option<Node>, String> {
    conn.query_row(
        "SELECT * FROM nodes WHERE fqn = ?1",
        params![fqn],
        node_from_row,
    )
    .optional()
    .map_err(|err| err.to_string())
}

fn get_node_by_id(conn: &Connection, id: &str) -> Result<Option<Node>, String> {
    conn.query_row(
        "SELECT * FROM nodes WHERE id = ?1",
        params![id],
        node_from_row,
    )
    .optional()
    .map_err(|err| err.to_string())
}

pub fn normalize_fts_query(query: Option<&str>) -> String {
    let terms: Vec<String> = query
        .unwrap_or("")
        .split_whitespace()
        .map(str::trim)
        .filter(|term| !term.is_empty())
        .map(|term| term.replace('"', "\"\""))
        .collect();
    if terms.is_empty() {
        return String::new();
    }
    terms
        .into_iter()
        .map(|term| format!("\"{term}\"*"))
        .collect::<Vec<_>>()
        .join(" OR ")
}

pub(crate) fn search_nodes_fts(conn: &Connection, query: &str, category: Option<&str>, limit: usize) -> Result<Vec<Node>, String> {
    let query = normalize_fts_query(Some(query));
    if query.is_empty() {
        return Ok(Vec::new());
    }
    let mut nodes = Vec::new();
    if let Some(cat) = category {
        let mut stmt = conn
            .prepare(
                "SELECT n.* FROM nodes_fts f
                 JOIN nodes n ON n.rowid = f.rowid
                 WHERE nodes_fts MATCH ?1 AND n.category = ?2
                 ORDER BY rank
                 LIMIT ?3",
            )
            .map_err(|err| err.to_string())?;
        let rows = stmt
            .query_map(params![query, cat, limit as i64], node_from_row)
            .map_err(|err| err.to_string())?;
        for row in rows {
            nodes.push(row.map_err(|err| err.to_string())?);
        }
    } else {
        let mut stmt = conn
            .prepare(
                "SELECT n.* FROM nodes_fts f
                 JOIN nodes n ON n.rowid = f.rowid
                 WHERE nodes_fts MATCH ?1
                 ORDER BY CASE WHEN n.category = 'SOURCE' THEN 0 ELSE 1 END, rank
                 LIMIT ?2",
            )
            .map_err(|err| err.to_string())?;
        let rows = stmt
            .query_map(params![query, limit as i64], node_from_row)
            .map_err(|err| err.to_string())?;
        for row in rows {
            nodes.push(row.map_err(|err| err.to_string())?);
        }
    }
    Ok(nodes)
}

fn get_callers(conn: &Connection, node_id: &str) -> Result<Vec<Node>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT n.*, e.type as edge_type, e.call_site_line
             FROM edges e JOIN nodes n ON n.id = e.source_id
             WHERE e.target_id = ?1
                OR e.target_id = '__unresolved__::' || (SELECT name FROM nodes WHERE id = ?2)",
        )
        .map_err(|err| err.to_string())?;
    collect_nodes(stmt.query_map(params![node_id, node_id], node_from_row))
}

fn get_callees(conn: &Connection, node_id: &str) -> Result<Vec<Node>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT DISTINCT n.*, e.type as edge_type, e.call_site_line
             FROM edges e JOIN nodes n
               ON (n.id = e.target_id OR e.target_id = '__unresolved__::' || n.name)
             WHERE e.source_id = ?1",
        )
        .map_err(|err| err.to_string())?;
    collect_nodes(stmt.query_map(params![node_id], node_from_row))
}

fn collect_nodes(
    rows: rusqlite::Result<rusqlite::MappedRows<'_, fn(&Row<'_>) -> rusqlite::Result<Node>>>,
) -> Result<Vec<Node>, String> {
    let mut nodes = Vec::new();
    for row in rows.map_err(|err| err.to_string())? {
        nodes.push(row.map_err(|err| err.to_string())?);
    }
    Ok(nodes)
}

fn symbol_candidate(node: &Node, match_reason: &str) -> Value {
    json!({
        "fqn": node.fqn,
        "name": node.name,
        "kind": node.node_type,
        "language": node.language.as_deref().unwrap_or("unknown"),
        "file_path": node.file_path,
        "line": node.start_line,
        "match_reason": match_reason,
    })
}

fn sha256_prefix(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    let digest = hasher.finalize();
    format!("{digest:x}").chars().take(6).collect()
}

fn parse_file(file_path: &str, abs_path: &Path) -> Result<ParseResult, String> {
    let ext = abs_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if ext == "pdf" {
        return Ok(parse_pdf_file(file_path, abs_path));
    }

    let source = fs::read_to_string(abs_path).map_err(|err| err.to_string())?;
    let result = match ext.as_str() {
        "py" => parse_python_file(file_path, &source),
        "cs" => parse_csharp_file(file_path, &source),
        "java" => parse_java_file(file_path, &source),
        "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => parse_c_file(file_path, &source),
        "ts" => parse_ts_file(file_path, &source, "typescript"),
        "tsx" => parse_ts_file(file_path, &source, "tsx"),
        "js" => parse_ts_file(file_path, &source, "javascript"),
        "jsx" => parse_ts_file(file_path, &source, "jsx"),
        "md" | "markdown" => parse_markdown_file(file_path, &source),
        "html" | "htm" => parse_html_file(file_path, &source),
        "css" => parse_css_file(file_path, &source),
        _ => return Err(format!("No parser found for: {file_path}")),
    };
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{LazyLock, Mutex};

    static ENV_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));
    static NEXT_TMP_ID: AtomicU64 = AtomicU64::new(0);

    fn temp_root(name: &str) -> PathBuf {
        let id = NEXT_TMP_ID.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!("cortex_mcp_{name}_{}_{}", std::process::id(), id))
    }

    #[test]
    fn normalize_fts_query_matches_phrase_prefix_shape() {
        assert_eq!(normalize_fts_query(None), "");
        assert_eq!(normalize_fts_query(Some("  ")), "");
        assert_eq!(normalize_fts_query(Some("foo bar")), "\"foo\"* OR \"bar\"*");
        assert_eq!(
            normalize_fts_query(Some("a   b\tc")),
            "\"a\"* OR \"b\"* OR \"c\"*"
        );
        assert_eq!(
            normalize_fts_query(Some(r#"foo "bar""#)),
            "\"foo\"* OR \"\"\"bar\"\"\"*"
        );
    }

    #[test]
    fn workspace_key_honors_override() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
        let old = env::var("CORTEX_WORKSPACE_KEY").ok();
        env::set_var("CORTEX_WORKSPACE_KEY", "shared-workspace");
        assert_eq!(workspace_key("repo"), "shared-workspace");
        match old {
            Some(value) => env::set_var("CORTEX_WORKSPACE_KEY", value),
            None => env::remove_var("CORTEX_WORKSPACE_KEY"),
        }
    }

    #[test]
    fn memories_db_path_uses_workspace_key_prefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
        let old = env::var("CORTEX_DATA_HOME").ok();
        let old_key = env::var("CORTEX_WORKSPACE_KEY").ok();
        env::set_var("CORTEX_DATA_HOME", "cortex-data-home");
        env::set_var("CORTEX_WORKSPACE_KEY", "workspace-key");
        let path = memories_db_path("repo");
        assert!(path.ends_with(
            Path::new("workspaces")
                .join("workspace-key")
                .join("memories.db")
        ));
        match old {
            Some(value) => env::set_var("CORTEX_DATA_HOME", value),
            None => env::remove_var("CORTEX_DATA_HOME"),
        }
        match old_key {
            Some(value) => env::set_var("CORTEX_WORKSPACE_KEY", value),
            None => env::remove_var("CORTEX_WORKSPACE_KEY"),
        }
    }

    #[test]
    fn global_memories_db_path_uses_data_root() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
        let old = env::var("CORTEX_DATA_HOME").ok();
        env::set_var("CORTEX_DATA_HOME", "cortex-data-home");
        let path = global_memories_db_path();
        assert!(path.ends_with(Path::new("data").join("memories.db")));
        match old {
            Some(value) => env::set_var("CORTEX_DATA_HOME", value),
            None => env::remove_var("CORTEX_DATA_HOME"),
        }
    }

    #[test]
    fn cortex_home_root_prefers_env_when_present() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
        let old = env::var("CORTEX_HOME").ok();
        let old_data = env::var("CORTEX_DATA_HOME").ok();
        let home = temp_root("home");
        fs::create_dir_all(&home).unwrap();
        env::set_var("CORTEX_HOME", &home);
        env::set_var("CORTEX_DATA_HOME", "cortex-data-home");
        let root = cortex_home_root("repo");
        assert_eq!(root, home);
        match old {
            Some(value) => env::set_var("CORTEX_HOME", value),
            None => env::remove_var("CORTEX_HOME"),
        }
        match old_data {
            Some(value) => env::set_var("CORTEX_DATA_HOME", value),
            None => env::remove_var("CORTEX_DATA_HOME"),
        }
    }

    #[test]
    fn search_merges_global_rules_and_workspace_memory() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|err| err.into_inner());
        let old_home = env::var("CORTEX_HOME").ok();
        let old_data = env::var("CORTEX_DATA_HOME").ok();

        let root = temp_root("search_merge");
        let cortex_home = root.join("cortex-home");
        let data_home = root.join("data-home");
        let workspace = root.join("workspace");
        fs::create_dir_all(cortex_home.join("rules")).unwrap();
        fs::create_dir_all(&workspace).unwrap();
        fs::write(
            cortex_home.join("rules").join("guide.md"),
            "# Global Rule\nshared-token",
        )
        .unwrap();

        env::set_var("CORTEX_HOME", &cortex_home);
        env::set_var("CORTEX_DATA_HOME", &data_home);

        let conn = open_connection(&workspace).unwrap();
        upsert_memory_record(
            &conn,
            "workspace-note",
            "default",
            "decision",
            "workspace shared-token note",
            None,
            None,
        )
        .unwrap();
        drop(conn);

        let memory_results = call_search_memory(&workspace, "shared-token", None).unwrap();
        let memory_text = memory_results.as_str().expect("memory result text");
        let parsed: Vec<Value> = serde_json::from_str(memory_text).expect("parse search result");
        assert!(parsed
            .iter()
            .any(|item| item.get("source_scope").and_then(Value::as_str) == Some("global")));
        assert!(parsed
            .iter()
            .any(|item| item.get("source_scope").and_then(Value::as_str) == Some("workspace")));

        let context = call_search_context(&workspace, "shared-token", Some(1000)).unwrap();
        assert!(context
            .get("capsule")
            .and_then(Value::as_str)
            .unwrap_or("")
            .contains("[global:"));
        assert!(context
            .get("capsule")
            .and_then(Value::as_str)
            .unwrap_or("")
            .contains("[workspace:"));

        match old_home {
            Some(value) => env::set_var("CORTEX_HOME", value),
            None => env::remove_var("CORTEX_HOME"),
        }
        match old_data {
            Some(value) => env::set_var("CORTEX_DATA_HOME", value),
            None => env::remove_var("CORTEX_DATA_HOME"),
        }
    }
}
