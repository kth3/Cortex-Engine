use std::collections::{HashMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use cortex_parsers::{
    parse_c_file, parse_csharp_file, parse_css_file, parse_html_file, parse_java_file,
    parse_markdown_file, parse_pdf_file, parse_python_file, parse_ts_file, ParseResult,
};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub type ToolResult = Result<Value, String>;

#[path = "edit.rs"]
mod edit;
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
const MEMORY_DB_FILENAME: &str = "memories.db";
const BOARD_STATE_DIRNAME: &str = "state";
const BOARD_JSON_FILENAME: &str = "board.json";
const SESSION_ID: &str = "rust-mcp";

#[derive(Debug, Clone)]
struct Node {
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

fn board_json_path(workspace: impl AsRef<Path>) -> PathBuf {
    workspace_data_dir(workspace)
        .join(BOARD_STATE_DIRNAME)
        .join(BOARD_JSON_FILENAME)
}

pub(crate) fn workspace_history_dir(workspace: impl AsRef<Path>) -> PathBuf {
    absolute_path(workspace).join(".cortex").join("history")
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

pub(crate) fn open_connection(workspace: impl AsRef<Path>) -> Result<Connection, String> {
    let path = memories_db_path(workspace);
    let conn = Connection::open(path).map_err(|err| err.to_string())?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;\
         PRAGMA busy_timeout=5000;\
         PRAGMA foreign_keys=ON;\
         PRAGMA cache_size=-2000;",
    )
    .map_err(|err| err.to_string())?;
    Ok(conn)
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

fn search_nodes_fts(conn: &Connection, query: &str, limit: usize) -> Result<Vec<Node>, String> {
    let query = normalize_fts_query(Some(query));
    if query.is_empty() {
        return Ok(Vec::new());
    }
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
    let mut nodes = Vec::new();
    for row in rows {
        nodes.push(row.map_err(|err| err.to_string())?);
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
    use std::sync::{LazyLock, Mutex};

    static ENV_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));

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
        let _guard = ENV_LOCK.lock().unwrap();
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
        let _guard = ENV_LOCK.lock().unwrap();
        let old = env::var("CORTEX_DATA_HOME").ok();
        let old_key = env::var("CORTEX_WORKSPACE_KEY").ok();
        env::set_var("CORTEX_DATA_HOME", "cortex-data-home");
        env::set_var("CORTEX_WORKSPACE_KEY", "workspace-key");
        let path = memories_db_path("repo");
        assert!(path.ends_with(r"workspaces\workspace-key\memories.db"));
        match old {
            Some(value) => env::set_var("CORTEX_DATA_HOME", value),
            None => env::remove_var("CORTEX_DATA_HOME"),
        }
        match old_key {
            Some(value) => env::set_var("CORTEX_WORKSPACE_KEY", value),
            None => env::remove_var("CORTEX_WORKSPACE_KEY"),
        }
    }
}
