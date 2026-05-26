use super::*;
use cortex_parsers::NodeRecord;

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
        for node in search_nodes_fts(&conn, name, None, limit * FTS_PROBE_MULTIPLIER)? {
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

    if candidates.len() < limit {
        for node in embedding::search_nodes_vec(&conn, name, limit * FTS_PROBE_MULTIPLIER)? {
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
            candidates.push(symbol_candidate(&node, "vector_match"));
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
    let workspace = absolute_path(workspace);
    let conn = open_connection(&workspace)?;
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
        let neighbors = graph_or_sql_neighbors(
            &workspace,
            &conn,
            &current.fqn,
            &current.id,
            direction,
            max_nodes,
        )?;
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
    let workspace = absolute_path(workspace);
    let conn = open_connection(&workspace)?;
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
        
        let current_node = get_node_by_id(&conn, &current)?.unwrap();
        for callee in graph_or_sql_neighbors(&workspace, &conn, &current_node.fqn, &current_node.id, "callees", max_nodes)? {
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

fn graph_or_sql_neighbors(
    workspace: &Path,
    conn: &Connection,
    node_fqn: &str,
    node_id: &str,
    direction: &str,
    limit: usize,
) -> Result<Vec<Node>, String> {
    let graph_path = crate::storage_tools::graph_store_path(workspace);
    let graph_direction = cortex_storage::graph::GraphDirection::from_str(direction);
    match cortex_storage::graph::graph_neighbors(&graph_path, node_fqn, graph_direction, limit) {
        Ok(fqns) if !fqns.is_empty() => fqns
            .into_iter()
            .filter_map(|fqn| get_node_by_fqn(conn, &fqn).transpose())
            .collect(),
        Ok(_) | Err(_) => {
            // Fallback to SQLite edges
            let mut neighbors = Vec::new();
            if direction == "callers" || direction == "both" {
                neighbors.extend(get_callers(conn, node_id)?);
            }
            if direction == "callees" || direction == "both" {
                neighbors.extend(get_callees(conn, node_id)?);
            }
            Ok(neighbors)
        }
    }
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
