use super::*;

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

pub(crate) fn snippet(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

#[derive(Debug, Clone)]
struct RankedContextLine {
    key: String,
    line: String,
    score: i64,
}

pub fn call_search_context(
    workspace: impl AsRef<Path>,
    query: &str,
    token_budget: Option<usize>,
) -> ToolResult {
    let token_budget = token_budget.unwrap_or(DEFAULT_SEARCH_TOKEN_BUDGET);
    let workspace = absolute_path(workspace);
    let workspace_conn = open_connection(&workspace)?;
    let global_conn = open_global_connection(&workspace)?;
    let mut lines = Vec::new();
    let mut seen = HashSet::new();

    collect_code_context_hits(&workspace_conn, query, &mut lines, &mut seen)?;
    collect_memory_context_hits(
        &workspace_conn,
        "workspace",
        query,
        None,
        &mut lines,
        &mut seen,
    )?;
    collect_memory_context_hits(&global_conn, "global", query, None, &mut lines, &mut seen)?;

    lines.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then_with(|| a.key.cmp(&b.key))
            .then_with(|| a.line.cmp(&b.line))
    });

    let mut capsule = lines
        .into_iter()
        .map(|item| item.line)
        .collect::<Vec<_>>()
        .join("\n");
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
    let workspace = absolute_path(workspace);
    let workspace_conn = open_connection(&workspace)?;
    let global_conn = open_global_connection(&workspace)?;
    let mut unified = Vec::new();
    let mut seen = HashSet::new();

    collect_code_deep_hits(&workspace_conn, query, &mut unified, &mut seen)?;
    collect_memory_deep_hits(
        &workspace_conn,
        "workspace",
        query,
        None,
        &mut unified,
        &mut seen,
    )?;
    collect_memory_deep_hits(&global_conn, "global", query, None, &mut unified, &mut seen)?;

    unified.sort_by(|a, b| {
        let a_score = a
            .get("_total_score")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        let b_score = b
            .get("_total_score")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        b_score
            .cmp(&a_score)
            .then_with(|| {
                a.get("source_scope")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("source_scope").and_then(Value::as_str).unwrap_or(""))
            })
            .then_with(|| {
                a.get("key")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(b.get("key").and_then(Value::as_str).unwrap_or(""))
            })
    });
    let total_seen = unified.len();
    let truncated = total_seen > limit;
    if truncated {
        unified.truncate(limit);
    }

    let capsule = call_search_context(&workspace, query, Some(DEFAULT_SEARCH_TOKEN_BUDGET))?;
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
            super::code::call_get_impact_graph(
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
        "truncated": truncated,
        "limit": limit,
        "returned_count": unified.len(),
        "total_seen": total_seen,
    }))
}

fn collect_code_context_hits(
    conn: &Connection,
    query: &str,
    lines: &mut Vec<RankedContextLine>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
    for (idx, node) in search_nodes_fts(conn, query, 8)?.into_iter().enumerate() {
        push_context_line(
            lines,
            seen,
            "code",
            "workspace",
            &node.fqn,
            format!(
                "[code:fts] {} {}:{}",
                node.fqn,
                node.file_path.unwrap_or_default(),
                node.start_line.unwrap_or_default()
            ),
            context_rank_score("code", "workspace", "fts_match", idx),
        );
    }
    for (idx, node) in embedding::search_nodes_vec(conn, query, 8)?
        .into_iter()
        .enumerate()
    {
        push_context_line(
            lines,
            seen,
            "code",
            "workspace",
            &node.fqn,
            format!(
                "[code:vector] {} {}:{}",
                node.fqn,
                node.file_path.unwrap_or_default(),
                node.start_line.unwrap_or_default()
            ),
            context_rank_score("code", "workspace", "vector_match", idx),
        );
    }
    Ok(())
}

fn collect_memory_context_hits(
    conn: &Connection,
    source_scope: &str,
    query: &str,
    category: Option<&str>,
    lines: &mut Vec<RankedContextLine>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
    for (idx, item) in search_memories_fts(conn, query, category, 5)?
        .into_iter()
        .enumerate()
    {
        push_context_line(
            lines,
            seen,
            "memory",
            source_scope,
            &item.key,
            format!(
                "[{source_scope}:{}:fts] {}: {}",
                item.category,
                item.key,
                snippet(&item.content, 180)
            ),
            context_rank_score("memory", source_scope, "fts_match", idx),
        );
    }
    for (idx, item) in embedding::search_memories_vec(conn, query, category, 5)?
        .into_iter()
        .enumerate()
    {
        push_context_line(
            lines,
            seen,
            "memory",
            source_scope,
            &item.key,
            format!(
                "[{source_scope}:{}:vector] {}: {}",
                item.category,
                item.key,
                snippet(&item.content, 180)
            ),
            context_rank_score("memory", source_scope, "vector_match", idx),
        );
    }
    Ok(())
}

fn collect_code_deep_hits(
    conn: &Connection,
    query: &str,
    unified: &mut Vec<Value>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
    for (idx, node) in search_nodes_fts(conn, query, 5)?.into_iter().enumerate() {
        push_deep_hit(
            unified,
            seen,
            "code",
            "workspace",
            &node.fqn,
            json!({
                "domain": "code",
                "source_scope": "workspace",
                "match_reason": "fts_match",
                "key": node.fqn,
                "category": node.node_type,
                "file_path": node.file_path.unwrap_or_default(),
                "snippet": node.name,
                "_total_score": context_rank_score("code", "workspace", "fts_match", idx),
            }),
        );
    }
    for (idx, node) in embedding::search_nodes_vec(conn, query, 5)?
        .into_iter()
        .enumerate()
    {
        push_deep_hit(
            unified,
            seen,
            "code",
            "workspace",
            &node.fqn,
            json!({
                "domain": "code",
                "source_scope": "workspace",
                "match_reason": "vector_match",
                "key": node.fqn,
                "category": node.node_type,
                "file_path": node.file_path.unwrap_or_default(),
                "snippet": node.name,
                "_total_score": context_rank_score("code", "workspace", "vector_match", idx),
            }),
        );
    }
    Ok(())
}

fn collect_memory_deep_hits(
    conn: &Connection,
    source_scope: &str,
    query: &str,
    category: Option<&str>,
    unified: &mut Vec<Value>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
    for (idx, item) in search_memories_fts(conn, query, category, 5)?
        .into_iter()
        .enumerate()
    {
        push_deep_hit(
            unified,
            seen,
            "memory",
            source_scope,
            &item.key,
            json!({
                "domain": "knowledge",
                "source_scope": source_scope,
                "match_reason": "fts_match",
                "key": item.key,
                "category": item.category,
                "snippet": snippet(&item.content, 180),
                "_total_score": context_rank_score("memory", source_scope, "fts_match", idx),
            }),
        );
    }
    for (idx, item) in embedding::search_memories_vec(conn, query, category, 5)?
        .into_iter()
        .enumerate()
    {
        push_deep_hit(
            unified,
            seen,
            "memory",
            source_scope,
            &item.key,
            json!({
                "domain": "knowledge",
                "source_scope": source_scope,
                "match_reason": "vector_match",
                "key": item.key,
                "category": item.category,
                "snippet": snippet(&item.content, 180),
                "_total_score": context_rank_score("memory", source_scope, "vector_match", idx),
            }),
        );
    }
    Ok(())
}

fn push_context_line(
    lines: &mut Vec<RankedContextLine>,
    seen: &mut HashSet<String>,
    domain: &str,
    source_scope: &str,
    key: &str,
    line: String,
    score: i64,
) {
    let dedupe_key = format!("{domain}::{source_scope}::{key}");
    if seen.insert(dedupe_key.clone()) {
        lines.push(RankedContextLine {
            key: dedupe_key,
            line,
            score,
        });
    }
}

fn push_deep_hit(
    unified: &mut Vec<Value>,
    seen: &mut HashSet<String>,
    domain: &str,
    source_scope: &str,
    key: &str,
    value: Value,
) {
    let dedupe_key = format!("{domain}::{source_scope}::{key}");
    if seen.insert(dedupe_key) {
        unified.push(value);
    }
}

fn context_rank_score(domain: &str, source_scope: &str, match_reason: &str, index: usize) -> i64 {
    let domain_bias = match domain {
        "code" => 3_000,
        "memory" => 2_000,
        _ => 0,
    };
    let scope_bias = match source_scope {
        "workspace" => 1_000,
        "global" => 700,
        _ => 0,
    };
    let reason_bias = match match_reason {
        "fts_match" => 500,
        "vector_match" => 250,
        _ => 0,
    };
    domain_bias + scope_bias + reason_bias - index as i64
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
