use std::collections::{HashMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub type ToolResult = Result<Value, String>;

const DEFAULT_FILE_OUTLINE_DETAIL: &str = "standard";
const DEFAULT_RESOLVE_LIMIT: usize = 5;
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

fn open_connection(workspace: impl AsRef<Path>) -> Result<Connection, String> {
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

pub fn call_get_index_status(workspace: impl AsRef<Path>) -> ToolResult {
    let conn = open_connection(workspace)?;
    let schema_version: String = conn
        .query_row(
            "SELECT value FROM meta WHERE key='schema_version'",
            [],
            |row| row.get(0),
        )
        .map_err(|err| err.to_string())?;
    Ok(json!({
        "total_nodes": count_table(&conn, "nodes")?,
        "total_edges": count_table(&conn, "edges")?,
        "total_files": count_table(&conn, "file_cache")?,
        "total_memories": count_table(&conn, "memories")?,
        "schema_version": schema_version,
    }))
}

pub fn call_read_file_with_hash(
    workspace: impl AsRef<Path>,
    file_path: impl AsRef<Path>,
) -> ToolResult {
    let workspace = absolute_path(workspace);
    let full_path = workspace.join(file_path.as_ref());
    let full_path = full_path.canonicalize().map_err(|err| err.to_string())?;
    if !full_path.starts_with(&workspace) {
        return Err("Path traversal blocked".to_string());
    }
    let content = fs::read_to_string(&full_path).map_err(|err| err.to_string())?;
    let lines = content
        .lines()
        .enumerate()
        .map(|(idx, line)| format!("{:4} | {} | {}", idx + 1, sha256_prefix(line), line))
        .collect::<Vec<_>>();
    Ok(Value::String(lines.join("\n")))
}

pub fn call_get_file_outline(
    workspace: impl AsRef<Path>,
    file_path: impl AsRef<Path>,
    detail: Option<&str>,
) -> ToolResult {
    run_python_helper(
        workspace,
        "get_file_outline",
        json!({
            "file_path": file_path.as_ref().to_string_lossy(),
            "detail": detail.unwrap_or(DEFAULT_FILE_OUTLINE_DETAIL),
        }),
    )
}

pub fn call_resolve_symbol(
    workspace: impl AsRef<Path>,
    name: &str,
    file_path: Option<&str>,
    language: Option<&str>,
    limit: Option<usize>,
) -> ToolResult {
    let limit = limit.unwrap_or(DEFAULT_RESOLVE_LIMIT);
    let conn = open_connection(workspace)?;
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();

    if let Some(node) = get_node_by_fqn(&conn, name)? {
        candidates.push(symbol_candidate(&node, "exact_fqn"));
        seen.insert(node.fqn);
    }

    if candidates.len() < limit {
        for node in search_nodes_fts(&conn, name, limit * FTS_PROBE_MULTIPLIER)? {
            if seen.contains(&node.fqn) {
                continue;
            }
            if file_path.is_some() && node.file_path.as_deref() != file_path {
                continue;
            }
            if language.is_some() && node.language.as_deref() != language {
                continue;
            }
            seen.insert(node.fqn.clone());
            candidates.push(symbol_candidate(&node, "fts_match"));
            if candidates.len() >= limit {
                break;
            }
        }
    }

    if candidates.is_empty() {
        return Ok(json!({
            "candidates": [],
            "count": 0,
            "next_suggestion": "try search_context with a broader query",
        }));
    }
    Ok(json!({ "candidates": candidates, "count": candidates.len() }))
}

pub fn call_get_impact_graph(
    workspace: impl AsRef<Path>,
    fqn: &str,
    direction: Option<&str>,
    max_depth: Option<u32>,
    max_nodes: Option<u32>,
) -> ToolResult {
    let conn = open_connection(workspace)?;
    let direction = direction.unwrap_or(DEFAULT_IMPACT_DIRECTION);
    let max_depth = max_depth.unwrap_or(DEFAULT_IMPACT_MAX_DEPTH);
    let max_nodes = max_nodes.unwrap_or(DEFAULT_IMPACT_MAX_NODES) as usize;
    let Some(root) = get_node_by_fqn(&conn, fqn)? else {
        return Ok(json!({ "error": format!("Symbol not found: {fqn}") }));
    };

    let mut visited = HashSet::new();
    let mut queue = VecDeque::from([(root.clone(), 0_u32)]);
    let mut impact_nodes = HashMap::from([(root.id.clone(), root)]);
    let mut total_seen = 1_u32;
    let mut truncated = false;

    while let Some((current, depth)) = queue.pop_front() {
        if depth >= max_depth || visited.contains(&current.id) {
            continue;
        }
        visited.insert(current.id.clone());
        let mut neighbors = Vec::new();
        if direction == "callers" || direction == "both" {
            neighbors.extend(get_callers(&conn, &current.id)?);
        }
        if direction == "callees" || direction == "both" {
            neighbors.extend(get_callees(&conn, &current.id)?);
        }
        for neighbor in neighbors {
            if impact_nodes.contains_key(&neighbor.id) {
                continue;
            }
            total_seen += 1;
            if impact_nodes.len() >= max_nodes {
                truncated = true;
                continue;
            }
            queue.push_back((neighbor.clone(), depth + 1));
            impact_nodes.insert(neighbor.id.clone(), neighbor);
        }
    }

    let returned = impact_nodes
        .values()
        .map(|node| Value::String(node.fqn.clone()))
        .collect::<Vec<_>>();
    Ok(json!({
        "fqn": fqn,
        "impact_nodes": returned,
        "truncated": truncated,
        "limit": max_nodes,
        "returned_count": returned.len(),
        "total_seen": total_seen,
    }))
}

pub fn call_find_execution_path(
    workspace: impl AsRef<Path>,
    from_fqn: &str,
    to_fqn: &str,
    max_depth: Option<u32>,
    max_nodes: Option<u32>,
) -> ToolResult {
    let conn = open_connection(workspace)?;
    let max_depth = max_depth.unwrap_or(DEFAULT_LOGIC_MAX_DEPTH);
    let max_nodes = max_nodes.unwrap_or(DEFAULT_LOGIC_MAX_NODES) as usize;
    let Some(start) = get_node_by_fqn(&conn, from_fqn)? else {
        return Ok(json!({ "error": "Start or end symbol not found." }));
    };
    let Some(end) = get_node_by_fqn(&conn, to_fqn)? else {
        return Ok(json!({ "error": "Start or end symbol not found." }));
    };

    let mut queue = VecDeque::from([vec![start.id.clone()]]);
    let mut visited = HashSet::new();
    let mut total_seen = 1_u32;
    let mut truncated = false;

    while let Some(path) = queue.pop_front() {
        let current = path.last().cloned().unwrap_or_default();
        if current == end.id {
            let mut fqns = Vec::new();
            for node_id in path {
                if let Some(node) = get_node_by_id(&conn, &node_id)? {
                    fqns.push(node.fqn);
                }
            }
            return Ok(json!({
                "path": fqns,
                "truncated": false,
                "limit": max_nodes,
                "returned_count": fqns.len(),
                "total_seen": total_seen,
            }));
        }
        if path.len().saturating_sub(1) as u32 >= max_depth {
            truncated = true;
            continue;
        }
        if visited.contains(&current) {
            continue;
        }
        visited.insert(current.clone());
        if visited.len() >= max_nodes {
            truncated = true;
            continue;
        }
        for callee in get_callees(&conn, &current)? {
            total_seen += 1;
            let mut next = path.clone();
            next.push(callee.id);
            queue.push_back(next);
        }
    }

    Ok(json!({
        "path": [],
        "truncated": truncated,
        "limit": max_nodes,
        "returned_count": 0,
        "total_seen": total_seen,
    }))
}

pub fn call_get_session_context(
    workspace: impl AsRef<Path>,
    token_budget: Option<u32>,
) -> ToolResult {
    let token_budget = token_budget.unwrap_or(DEFAULT_SESSION_TOKEN_BUDGET) as usize;
    let conn = open_connection(&workspace)?;
    let mut sections = Vec::new();
    let mut total_chars = 0_usize;

    append_memory_entries(
        &conn,
        &mut sections,
        &mut total_chars,
        token_budget,
        "SELECT key, content, updated_at FROM memories WHERE category = 'decision' ORDER BY updated_at DESC LIMIT 5",
        "decision",
    )?;
    append_memory_entries(
        &conn,
        &mut sections,
        &mut total_chars,
        token_budget,
        "SELECT key, content, updated_at FROM memories WHERE category = 'pattern' ORDER BY updated_at DESC LIMIT 3",
        "pattern",
    )?;
    append_popular_entries(&conn, &mut sections, &mut total_chars, token_budget)?;
    append_contract_entries(workspace, &mut sections, &mut total_chars);

    Ok(json!({
        "context": sections.join("\n"),
        "totalChars": total_chars,
        "itemCount": sections.len(),
    }))
}

fn append_memory_entries(
    conn: &Connection,
    sections: &mut Vec<String>,
    total_chars: &mut usize,
    token_budget: usize,
    sql: &str,
    category: &str,
) -> Result<(), String> {
    let mut stmt = conn.prepare(sql).map_err(|err| err.to_string())?;
    let rows = stmt
        .query_map([], |row| {
            let key: String = row.get("key")?;
            let content: String = row.get("content")?;
            Ok((key, content))
        })
        .map_err(|err| err.to_string())?;
    for row in rows {
        let (key, content) = row.map_err(|err| err.to_string())?;
        let entry = format!("[{category}] {key}: {}", snippet(&content, 150));
        append_with_budget(sections, total_chars, token_budget, entry);
    }
    Ok(())
}

fn append_popular_entries(
    conn: &Connection,
    sections: &mut Vec<String>,
    total_chars: &mut usize,
    token_budget: usize,
) -> Result<(), String> {
    let mut stmt = conn
        .prepare(
            "SELECT key, category, content, access_count FROM memories
             WHERE access_count > 0 ORDER BY access_count DESC LIMIT 5",
        )
        .map_err(|err| err.to_string())?;
    let rows = stmt
        .query_map([], |row| {
            let key: String = row.get("key")?;
            let category: String = row.get("category")?;
            let content: String = row.get("content")?;
            let access_count: i64 = row.get("access_count")?;
            Ok((key, category, content, access_count))
        })
        .map_err(|err| err.to_string())?;
    for row in rows {
        let (key, category, content, access_count) = row.map_err(|err| err.to_string())?;
        if sections.iter().any(|section| section.contains(&key)) {
            continue;
        }
        let entry = format!(
            "[{category}] {key} (hits:{access_count}): {}",
            snippet(&content, 100)
        );
        append_with_budget(sections, total_chars, token_budget, entry);
    }
    Ok(())
}

fn append_contract_entries(
    workspace: impl AsRef<Path>,
    sections: &mut Vec<String>,
    total_chars: &mut usize,
) {
    let path = board_json_path(workspace);
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    let Ok(board) = serde_json::from_str::<Value>(&text) else {
        return;
    };
    let Some(lanes) = board.get("lanes").and_then(Value::as_object) else {
        return;
    };
    for (lane_id, lane) in lanes {
        if let Some(contract_id) = lane.get("contract_id").and_then(Value::as_str) {
            let entry = format!("[contract] lane={lane_id}: {contract_id}");
            sections.push(entry.clone());
            *total_chars += entry.len();
        }
    }
}

fn append_with_budget(
    sections: &mut Vec<String>,
    total_chars: &mut usize,
    token_budget: usize,
    entry: String,
) {
    if *total_chars + entry.len() <= token_budget {
        *total_chars += entry.len();
        sections.push(entry);
    }
}

fn snippet(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

fn python_helper_script() -> &'static str {
    r#"
import json
import pathlib
import sys
import types

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "scripts"))
args = json.loads(sys.argv[2])
ctx = types.SimpleNamespace(workspace=str(root), session_id="rust-mcp")

from cortex.skeletons.generator import generate_skeleton
result = generate_skeleton(ctx.workspace, args["file_path"], args.get("detail", "standard"))
print(json.dumps(result, ensure_ascii=False))
"#
}

fn parse_python_output(output: Output) -> ToolResult {
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            "python helper failed".to_string()
        } else {
            format!("python helper failed: {stderr}")
        });
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let payload = stdout
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("")
        .trim();
    serde_json::from_str(payload)
        .map_err(|err| format!("failed to parse python helper output: {err}; payload={payload}"))
}

fn run_python_helper(workspace: impl AsRef<Path>, _action: &str, args: Value) -> ToolResult {
    let workspace = absolute_path(workspace);
    let payload = serde_json::to_string(&args).map_err(|err| err.to_string())?;
    let candidates = [("python", vec!["-c"]), ("py", vec!["-3", "-c"])];
    let mut last_error = None;
    for (exe, prefix) in candidates {
        let mut command = Command::new(exe);
        command
            .args(prefix)
            .arg(python_helper_script())
            .arg(workspace.as_os_str())
            .arg(&payload)
            .current_dir(&workspace)
            .env("CORTEX_WORKSPACE", &workspace)
            .env("CORTEX_DATA_HOME", data_home())
            .env("CORTEX_WORKSPACE_KEY", workspace_key(&workspace))
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8");
        match command.output() {
            Ok(output) => return parse_python_output(output),
            Err(err) => last_error = Some(format!("{exe}: {err}")),
        }
    }
    Err(last_error.unwrap_or_else(|| "unable to launch python helper".to_string()))
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
        assert_eq!(workspace_key("C:\\workspace\\repo"), "shared-workspace");
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
        env::set_var("CORTEX_DATA_HOME", r"C:\\workspace\\cortex-data-home");
        env::set_var("CORTEX_WORKSPACE_KEY", "workspace-key");
        let path = memories_db_path(r"C:\\workspace\\repo");
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
