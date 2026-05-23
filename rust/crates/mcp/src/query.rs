use super::*;

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

pub(crate) fn snippet(text: &str, max_chars: usize) -> String {
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
