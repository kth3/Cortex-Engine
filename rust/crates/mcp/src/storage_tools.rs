use std::collections::{HashMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use cortex_parsers::{
    parse_c_file, parse_csharp_file, parse_css_file, parse_html_file, parse_java_file,
    parse_markdown_file, parse_pdf_file, parse_python_file, parse_ts_file, NodeRecord, ParseResult,
};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub type ToolResult = Result<Value, String>;

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

fn workspace_history_dir(workspace: impl AsRef<Path>) -> PathBuf {
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

fn generate_file_skeleton(nodes: &[NodeRecord], detail: &str) -> String {
    let mut sorted = nodes.to_vec();
    sorted.sort_by_key(|node| node.start_line);
    sorted
        .iter()
        .filter_map(|node| node_skeleton(node, detail))
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn node_skeleton(node: &NodeRecord, detail: &str) -> Option<String> {
    if detail == "minimal" {
        return node
            .skeleton_minimal
            .clone()
            .or_else(|| node.signature.clone())
            .filter(|value| !value.is_empty());
    }
    if detail == "detailed" {
        let body = node.raw_body.lines().take(5).collect::<Vec<_>>().join("\n");
        if !body.is_empty() {
            return Some(format!("{body} ... (truncated)"));
        }
    }
    node.skeleton_standard
        .clone()
        .or_else(|| node.signature.clone())
        .filter(|value| !value.is_empty())
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
    let workspace = absolute_path(workspace);
    let file_path_text = file_path.as_ref().to_string_lossy().replace('\\', "/");
    let abs_path = workspace.join(file_path.as_ref());
    if !abs_path.exists() {
        return Ok(Value::String(format!(
            "File not found: {}",
            abs_path.display()
        )));
    }

    let parse_result = parse_file(&file_path_text, &abs_path)?;
    Ok(Value::String(generate_file_skeleton(
        &parse_result.nodes,
        detail.unwrap_or("standard"),
    )))
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

pub fn call_search_context(
    workspace: impl AsRef<Path>,
    query: &str,
    token_budget: Option<usize>,
) -> ToolResult {
    let token_budget = token_budget.unwrap_or(DEFAULT_SEARCH_TOKEN_BUDGET);
    let conn = open_connection(workspace)?;
    let mut lines = Vec::new();

    for node in search_nodes_fts(&conn, query, 8)? {
        lines.push(format!(
            "[code] {} {}:{}",
            node.fqn,
            node.file_path.unwrap_or_default(),
            node.start_line.unwrap_or_default()
        ));
    }
    for item in search_memories_fts(&conn, query, None, 5)? {
        lines.push(format!(
            "[{}] {}: {}",
            item.category,
            item.key,
            snippet(&item.content, 180)
        ));
    }

    let mut capsule = lines.join("\n");
    if capsule.len() > token_budget {
        capsule = capsule.chars().take(token_budget).collect();
    }
    Ok(json!({
        "capsule": capsule,
        "chars_used": capsule.len(),
        "tokens_estimated": capsule.len() / 4,
        "token_budget": token_budget,
    }))
}

pub fn call_search_deep_context(
    workspace: impl AsRef<Path>,
    query: &str,
    limit: Option<usize>,
) -> ToolResult {
    let limit = limit.unwrap_or(DEFAULT_DEEP_CONTEXT_LIMIT);
    let conn = open_connection(&workspace)?;
    let mut unified = Vec::new();

    for node in search_nodes_fts(&conn, query, limit)? {
        unified.push(json!({
            "domain": "code",
            "key": node.fqn,
            "category": node.node_type,
            "file_path": node.file_path.unwrap_or_default(),
            "snippet": node.name,
            "_total_score": 0.0,
        }));
    }
    for memory in search_memories_fts(&conn, query, None, limit)? {
        unified.push(json!({
            "domain": "knowledge",
            "key": memory.key,
            "category": memory.category,
            "snippet": snippet(&memory.content, 180),
            "_total_score": 0.0,
        }));
    }
    unified.truncate(limit);

    let capsule = call_search_context(workspace, query, Some(DEFAULT_SEARCH_TOKEN_BUDGET))?;
    let capsule_text = capsule
        .get("capsule")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let impact_summary = unified
        .iter()
        .find(|item| item.get("domain").and_then(Value::as_str) == Some("code"))
        .and_then(|item| item.get("key").and_then(Value::as_str))
        .and_then(|fqn| {
            call_get_impact_graph(
                env::var("CORTEX_WORKSPACE").unwrap_or_else(|_| ".".into()),
                fqn,
                None,
                Some(2),
                Some(10),
            )
            .ok()
        })
        .and_then(|value| value.get("impact_nodes").cloned())
        .unwrap_or_else(|| json!([]));

    Ok(json!({
        "unified_context": unified,
        "capsule": capsule_text,
        "capsule_chars": capsule_text.len(),
        "impact_summary": impact_summary,
        "truncated": false,
        "limit": limit,
        "returned_count": unified.len(),
        "total_seen": unified.len(),
    }))
}

pub fn call_get_file_git_history(
    workspace: impl AsRef<Path>,
    file_path: &str,
    limit: Option<usize>,
) -> ToolResult {
    let output = Command::new("git")
        .args([
            "log",
            "--follow",
            "--date=iso",
            &format!("-n{}", limit.unwrap_or(5)),
            "--pretty=format:%H%x1f%an%x1f%ad%x1f%s",
            "--",
            file_path,
        ])
        .current_dir(absolute_path(workspace))
        .output()
        .map_err(|err| err.to_string())?;
    if !output.status.success() {
        return Ok(json!({"error": String::from_utf8_lossy(&output.stderr).trim()}));
    }
    let commits = String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let parts = line.split('\x1f').collect::<Vec<_>>();
            if parts.len() == 4 {
                Some(json!({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3],
                }))
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    Ok(json!({"file_path": file_path, "commits": commits}))
}

pub fn call_replace_exact_text(
    workspace: impl AsRef<Path>,
    file_path: &str,
    old_content: &str,
    new_content: &str,
) -> ToolResult {
    let workspace = absolute_path(workspace);
    let full_path = workspace
        .join(file_path)
        .canonicalize()
        .map_err(|err| err.to_string())?;
    if !full_path.starts_with(&workspace) {
        return Ok(json!({"error": "Path traversal blocked"}));
    }
    let before = fs::read_to_string(&full_path).map_err(|err| err.to_string())?;
    let Some(index) = before.find(old_content) else {
        return Ok(json!({
            "error": "Content mismatch",
            "reason": "The code block was not found.",
            "tip": "Re-read the file with hashes and ensure old_content matches.",
        }));
    };
    let mut after = String::with_capacity(before.len() - old_content.len() + new_content.len());
    after.push_str(&before[..index]);
    after.push_str(new_content);
    after.push_str(&before[index + old_content.len()..]);
    fs::write(&full_path, &after).map_err(|err| err.to_string())?;
    record_edit_event(&workspace, file_path, &before, &after)?;
    save_observation(
        &workspace,
        "edit",
        &format!("Strict edit: {file_path}"),
        Some(file_path),
    )?;
    Ok(json!({"success": true, "match_type": "exact"}))
}

pub fn call_sync_session_memory(workspace: impl AsRef<Path>, task_desc: &str) -> ToolResult {
    let branch = git_text(&workspace, &["rev-parse", "--abbrev-ref", "HEAD"])
        .unwrap_or_else(|| "unknown".to_string());
    let key = format!("session-sync-{}", now_unix());
    let relationships = json!({"jira_issues": [], "modifies": [], "branch": branch});
    write_memory_row(
        &workspace,
        &key,
        "decision",
        task_desc,
        Some(json!(["session-sync", "auto-generated", "autonomous-rag"])),
        Some(relationships.clone()),
    )?;
    Ok(json!({
        "success": true,
        "key": key,
        "extracted_relationships": relationships,
        "markdown_synced": false,
    }))
}

pub fn call_write_memory(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let key = arg_str(args, "key");
    let category = arg_str(args, "category");
    let content = arg_str(args, "content");
    let tags = args.get("tags").cloned();
    let relationships = args.get("relationships").cloned();
    write_memory_row(&workspace, key, category, content, tags, relationships)?;
    Ok(json!({"success": true, "key": key, "auto_promoted_to": promoted_file(category)}))
}

pub fn call_consolidate_memory(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let new_key = arg_str(args, "new_key");
    let category = arg_str(args, "category");
    let content = arg_str(args, "content");
    let old_keys = args
        .get("old_keys")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let dry_run = args.get("dry_run").and_then(Value::as_bool).unwrap_or(true);
    let would_write = json!({
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags").cloned().unwrap_or_else(|| json!([])),
        "relationships": args.get("relationships").cloned().unwrap_or_else(|| json!({})),
    });
    if dry_run {
        return Ok(json!({
            "executed": false,
            "would_delete": old_keys,
            "would_write": would_write,
            "auto_promoted_to": promoted_file(category),
            "note": "dry_run=true (default). actual merge/delete skipped.",
        }));
    }
    let conn = open_connection(&workspace)?;
    for key in &old_keys {
        if let Some(key) = key.as_str() {
            conn.execute("DELETE FROM memories WHERE key = ?1", params![key])
                .map_err(|err| err.to_string())?;
        }
    }
    write_memory_row(
        &workspace,
        new_key,
        category,
        content,
        args.get("tags").cloned(),
        args.get("relationships").cloned(),
    )?;
    Ok(json!({
        "executed": true,
        "success": true,
        "consolidated_key": new_key,
        "deleted_old_fragments": old_keys.len(),
        "auto_promoted_to": promoted_file(category),
        "would_delete": old_keys,
        "would_write": would_write,
    }))
}

pub fn call_read_memory(workspace: impl AsRef<Path>, key: &str) -> ToolResult {
    let conn = open_connection(workspace)?;
    let memory = conn
        .query_row(
            "SELECT key, project_id, category, content, tags, relationships, access_count, created_at, updated_at FROM memories WHERE key = ?1",
            params![key],
            memory_from_row,
        )
        .optional()
        .map_err(|err| err.to_string())?;
    if let Some(memory) = memory {
        conn.execute(
            "UPDATE memories SET access_count = access_count + 1 WHERE key = ?1",
            params![key],
        )
        .map_err(|err| err.to_string())?;
        return Ok(memory.to_value());
    }
    Ok(json!({"error": format!("Key '{key}' not found")}))
}

pub fn call_save_observation(workspace: impl AsRef<Path>, content: &str) -> ToolResult {
    save_observation(workspace, "insight", content, None)?;
    Ok(Value::Bool(true))
}

pub fn call_search_memory(
    workspace: impl AsRef<Path>,
    query: &str,
    category: Option<&str>,
) -> ToolResult {
    let results = search_memories_fts(&open_connection(workspace)?, query, category, 5)?
        .into_iter()
        .map(|memory| {
            json!({
                "key": memory.key,
                "category": memory.category,
                "content": snippet(&memory.content, 200),
                "score": 0.0,
            })
        })
        .collect::<Vec<_>>();
    Ok(Value::String(
        serde_json::to_string_pretty(&results).map_err(|err| err.to_string())?,
    ))
}

pub fn call_create_task_contract(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let workspace = absolute_path(workspace);
    let lane_id = arg_str(args, "lane_id");
    let task_name = arg_str(args, "task_name");
    let instructions = arg_str(args, "instructions");
    let files = args
        .get("files_to_modify")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let artifacts_dir = workspace.join(".cortex").join("artifacts");
    fs::create_dir_all(&artifacts_dir).map_err(|err| err.to_string())?;
    let contract_id = format!("contract_{}_{}.md", lane_id, now_unix());
    let path = artifacts_dir.join(&contract_id);
    let targeted = if files.is_empty() {
        "Not specified".to_string()
    } else {
        files.join(", ")
    };
    let content = format!(
        "# Task Contract: {task_name}\n- **Lane**: {lane_id}\n- **Created**: {}\n- **Session**: {SESSION_ID}\n\n## Instructions\n{instructions}\n\n## Targeted Files\n{targeted}\n\n## Constraints\n- Use strict replacement tools for file edits.\n- Clear outstanding todos before release.\n",
        now_text()
    );
    fs::write(&path, content).map_err(|err| err.to_string())?;
    save_observation(
        &workspace,
        "decision",
        &format!("Contract created: {contract_id}"),
        Some(&path.to_string_lossy()),
    )?;
    Ok(json!({"contract_id": contract_id, "path": path.to_string_lossy()}))
}

pub fn call_manage_todo(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let workspace = absolute_path(workspace);
    let action = arg_str(args, "action");
    let todo_path = workspace_history_dir(&workspace).join("todo.json");
    fs::create_dir_all(todo_path.parent().unwrap_or(&workspace)).map_err(|err| err.to_string())?;
    let mut data = fs::read_to_string(&todo_path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .unwrap_or_else(|| json!({"todos": []}));
    let todos = data
        .get_mut("todos")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "invalid todo state".to_string())?;
    let response = match action {
        "add" => {
            let next_id = todos
                .iter()
                .filter_map(|todo| todo.get("id").and_then(Value::as_str))
                .filter_map(|id| id.parse::<u64>().ok())
                .max()
                .unwrap_or(0)
                + 1;
            todos.push(json!({
                "id": next_id.to_string(),
                "task": args.get("task").and_then(Value::as_str).unwrap_or(""),
                "done": false,
                "created_at": now_text(),
            }));
            json!({"success": true, "id": next_id.to_string()})
        }
        "check" => {
            let task_id = args.get("task_id").and_then(Value::as_str).unwrap_or("");
            for todo in todos {
                if todo.get("id").and_then(Value::as_str) == Some(task_id) {
                    todo["done"] = Value::Bool(true);
                    todo["completed_at"] = Value::String(now_text());
                }
            }
            json!({"success": true})
        }
        "clear" => {
            data = json!({"todos": []});
            json!({"success": true})
        }
        _ => data.clone(),
    };
    fs::write(
        &todo_path,
        serde_json::to_string_pretty(&data).map_err(|err| err.to_string())?,
    )
    .map_err(|err| err.to_string())?;
    Ok(response)
}

#[derive(Debug)]
struct Memory {
    key: String,
    project_id: String,
    category: String,
    content: String,
    tags: String,
    relationships: String,
    access_count: i64,
    created_at: i64,
    updated_at: i64,
}

impl Memory {
    fn to_value(&self) -> Value {
        json!({
            "key": self.key,
            "project_id": self.project_id,
            "category": self.category,
            "content": self.content,
            "tags": serde_json::from_str::<Value>(&self.tags).unwrap_or_else(|_| json!([])),
            "relationships": serde_json::from_str::<Value>(&self.relationships).unwrap_or_else(|_| json!({})),
            "access_count": self.access_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })
    }
}

fn memory_from_row(row: &Row<'_>) -> rusqlite::Result<Memory> {
    Ok(Memory {
        key: row.get("key")?,
        project_id: row.get("project_id")?,
        category: row.get("category")?,
        content: row.get("content")?,
        tags: row.get("tags")?,
        relationships: row.get("relationships")?,
        access_count: row.get("access_count")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
    })
}

fn search_memories_fts(
    conn: &Connection,
    query: &str,
    category: Option<&str>,
    limit: usize,
) -> Result<Vec<Memory>, String> {
    let query = normalize_fts_query(Some(query));
    if query.is_empty() {
        return Ok(Vec::new());
    }
    let sql = if category.is_some() {
        "SELECT m.* FROM memories_fts f
         JOIN memories m ON m.rowid = f.rowid
         WHERE memories_fts MATCH ?1 AND m.category = ?2
         ORDER BY rank LIMIT ?3"
    } else {
        "SELECT m.* FROM memories_fts f
         JOIN memories m ON m.rowid = f.rowid
         WHERE memories_fts MATCH ?1
         ORDER BY rank LIMIT ?2"
    };
    let mut stmt = conn.prepare(sql).map_err(|err| err.to_string())?;
    let mut out = Vec::new();
    if let Some(category) = category {
        let rows = stmt
            .query_map(params![query, category, limit as i64], memory_from_row)
            .map_err(|err| err.to_string())?;
        for row in rows {
            out.push(row.map_err(|err| err.to_string())?);
        }
    } else {
        let rows = stmt
            .query_map(params![query, limit as i64], memory_from_row)
            .map_err(|err| err.to_string())?;
        for row in rows {
            out.push(row.map_err(|err| err.to_string())?);
        }
    }
    Ok(out)
}

fn write_memory_row(
    workspace: impl AsRef<Path>,
    key: &str,
    category: &str,
    content: &str,
    tags: Option<Value>,
    relationships: Option<Value>,
) -> Result<(), String> {
    if key.is_empty() {
        return Err("memory key is required".to_string());
    }
    let conn = open_connection(workspace)?;
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
             SET category = ?1, content = ?2, tags = ?3, relationships = ?4,
                 updated_at = ?5, access_count = access_count + 1
             WHERE key = ?6",
            params![category, content, tags, relationships, now, key],
        )
        .map_err(|err| err.to_string())?;
    } else {
        conn.execute(
            "INSERT INTO memories
             (key, project_id, category, content, tags, relationships, created_at, updated_at)
             VALUES (?1, 'default', ?2, ?3, ?4, ?5, ?6, ?7)",
            params![key, category, content, tags, relationships, now, now],
        )
        .map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn save_observation(
    workspace: impl AsRef<Path>,
    obs_type: &str,
    content: &str,
    file_path: Option<&str>,
) -> Result<(), String> {
    let conn = open_connection(workspace)?;
    conn.execute(
        "INSERT INTO observations (session_id, type, content, file_paths, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![SESSION_ID, obs_type, content, file_path, now_unix()],
    )
    .map_err(|err| err.to_string())?;
    Ok(())
}

fn record_edit_event(
    workspace: impl AsRef<Path>,
    file_path: &str,
    before: &str,
    after: &str,
) -> Result<(), String> {
    let conn = open_connection(&workspace)?;
    let normalized = normalize_event_path(workspace, file_path)?;
    let before_hash = full_sha256(before);
    let after_hash = full_sha256(after);
    let now = now_text();
    let existing = conn
        .query_row(
            "SELECT id FROM file_edit_events
             WHERE file_path = ?1 AND before_hash = ?2 AND after_hash = ?3 AND session_id = ?4",
            params![normalized, before_hash, after_hash, SESSION_ID],
            |row| row.get::<_, i64>(0),
        )
        .optional()
        .map_err(|err| err.to_string())?;
    if let Some(id) = existing {
        conn.execute(
            "UPDATE file_edit_events
             SET updated_at = ?1, tool_name = 'replace_exact_text', edit_summary = ?2
             WHERE id = ?3",
            params![now, format!("Strict edit: {file_path}"), id],
        )
        .map_err(|err| err.to_string())?;
    } else {
        conn.execute(
            "INSERT INTO file_edit_events
             (file_path, before_hash, after_hash, line_range, tool_name, event_sources,
              session_id, edit_summary, created_at, updated_at)
             VALUES (?1, ?2, ?3, NULL, 'replace_exact_text', 'cortex_mcp', ?4, ?5, ?6, ?7)",
            params![
                normalized,
                before_hash,
                after_hash,
                SESSION_ID,
                format!("Strict edit: {file_path}"),
                now,
                now
            ],
        )
        .map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn normalize_event_path(workspace: impl AsRef<Path>, file_path: &str) -> Result<String, String> {
    let workspace = absolute_path(workspace);
    let target = workspace.join(file_path);
    let normalized = target.canonicalize().unwrap_or(target);
    if !normalized.starts_with(&workspace) {
        return Err(format!(
            "Invalid edit event path outside workspace: {file_path}"
        ));
    }
    let rel = normalized
        .strip_prefix(&workspace)
        .map_err(|err| err.to_string())?
        .to_string_lossy()
        .replace('\\', "/");
    if cfg!(windows) {
        Ok(rel.to_ascii_lowercase())
    } else {
        Ok(rel)
    }
}

fn full_sha256(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

fn arg_str<'a>(args: &'a Value, key: &str) -> &'a str {
    args.get(key).and_then(Value::as_str).unwrap_or("")
}

fn promoted_file(category: &str) -> Option<&'static str> {
    match category {
        "decision" | "architecture" => Some("decisions.md"),
        "pattern" | "convention" | "rule" | "protocol" => Some("patterns.md"),
        _ => None,
    }
}

fn git_text(workspace: impl AsRef<Path>, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(absolute_path(workspace))
        .output()
        .ok()?;
    if output.status.success() {
        Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        None
    }
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
