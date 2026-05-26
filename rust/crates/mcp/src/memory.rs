use super::*;

#[derive(Debug, Clone)]
struct MemorySearchHit {
    key: String,
    category: String,
    content: String,
    source_scope: String,
    match_reason: String,
    score: i64,
}

pub fn call_sync_session_memory(workspace: impl AsRef<Path>, task_desc: &str) -> ToolResult {
    let workspace = absolute_path(workspace);
    let branch = git_text(&workspace, &["rev-parse", "--abbrev-ref", "HEAD"])
        .unwrap_or_else(|| "unknown".to_string());
    
    let status1 = git_text(&workspace, &["diff", "--name-only", "HEAD"]).unwrap_or_default();
    let status2 = git_text(&workspace, &["log", "-1", "--name-only", "--pretty=format:"]).unwrap_or_default();
    let mut modified_files: Vec<String> = status1.lines().chain(status2.lines())
        .map(str::trim).filter(|s| !s.is_empty()).map(String::from).collect();
    modified_files.sort();
    modified_files.dedup();
    let jira_issues: Vec<String> = Vec::new();

    let key = format!("session-sync-{}", now_unix());
    let relationships = json!({"jira_issues": jira_issues, "modifies": modified_files, "branch": branch});
    write_memory_row(
        &workspace,
        &key,
        "decision",
        task_desc,
        Some(json!(["session-sync", "auto-generated", "autonomous-rag"])),
        Some(relationships.clone()),
    )?;
    
    let md_res = append_session_sync_markdown(&workspace, task_desc, &branch, &jira_issues, &modified_files);
    update_memory_yaml_if_exists(&workspace, &branch);
    
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    Ok(json!({
        "success": true,
        "key": key,
        "extracted_relationships": relationships,
        "markdown_synced": md_res.is_ok(),
        "inbox_items": inbox_items,
    }))
}

pub fn call_write_memory(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let workspace = absolute_path(workspace);
    let key = arg_str(args, "key");
    let category = arg_str(args, "category");
    let content = arg_str(args, "content");
    let tags = args.get("tags").cloned();
    let relationships = args.get("relationships").cloned();
    write_memory_row(&workspace, key, category, content, tags, relationships)?;
    let _ = append_promoted_memory_log(&workspace, category, key, content);
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    Ok(json!({
        "success": true,
        "key": key,
        "auto_promoted_to": promoted_file(category),
        "inbox_items": inbox_items,
    }))
}

pub fn call_consolidate_memory(workspace: impl AsRef<Path>, args: &Value) -> ToolResult {
    let workspace = absolute_path(workspace);
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
    let _ = append_promoted_memory_log(&workspace, category, new_key, content);
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    Ok(json!({
        "executed": true,
        "success": true,
        "consolidated_key": new_key,
        "deleted_old_fragments": old_keys.len(),
        "auto_promoted_to": promoted_file(category),
        "would_delete": old_keys,
        "would_write": would_write,
        "inbox_items": inbox_items,
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
    let workspace = absolute_path(workspace);
    save_observation(&workspace, "insight", content, None)?;
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    Ok(json!({"success": true, "inbox_items": inbox_items}))
}

pub fn call_search_memory(
    workspace: impl AsRef<Path>,
    query: &str,
    category: Option<&str>,
) -> ToolResult {
    let workspace = absolute_path(workspace);
    let workspace_conn = open_connection(&workspace)?;
    let global_conn = open_global_connection(&workspace)?;
    let mut hits = collect_memory_hits(&workspace_conn, "workspace", query, category, 5)?;
    hits.extend(collect_memory_hits(
        &global_conn,
        "global",
        query,
        category,
        5,
    )?);
    hits.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then_with(|| a.source_scope.cmp(&b.source_scope))
            .then_with(|| a.key.cmp(&b.key))
    });
    let results = hits
        .into_iter()
        .map(|hit| {
            json!({
                "key": hit.key,
                "category": hit.category,
                "content": snippet(&hit.content, 200),
                "source_scope": hit.source_scope,
                "match_reason": hit.match_reason,
                "score": hit.score,
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
    
    // Acquire Relay lock (matching Python orchestration behavior)
    let _ = std::process::Command::new("uv")
        .args(&["run", "--project", ".agents", "python", ".agents/scripts/relay.py", "acquire", "rust-mcp", &task_name, &lane_id])
        .current_dir(&workspace)
        .status();

    save_observation(
        &workspace,
        "decision",
        &format!("Contract created: {contract_id}"),
        Some(&path.to_string_lossy()),
    )?;
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    Ok(
        json!({"contract_id": contract_id, "path": path.to_string_lossy(), "inbox_items": inbox_items}),
    )
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
pub(crate) struct Memory {
    pub(crate) key: String,
    pub(crate) project_id: String,
    pub(crate) category: String,
    pub(crate) content: String,
    pub(crate) tags: String,
    pub(crate) relationships: String,
    pub(crate) access_count: i64,
    pub(crate) created_at: i64,
    pub(crate) updated_at: i64,
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

pub(crate) fn search_memories_fts(
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
    let conn = open_connection(workspace)?;
    upsert_memory_record(
        &conn,
        key,
        "default",
        category,
        content,
        tags,
        relationships,
    )
}

fn collect_memory_hits(
    conn: &Connection,
    source_scope: &str,
    query: &str,
    category: Option<&str>,
    limit: usize,
) -> Result<Vec<MemorySearchHit>, String> {
    let mut hits = Vec::new();
    let mut seen = HashSet::new();

    for (idx, memory) in search_memories_fts(conn, query, category, limit)?
        .into_iter()
        .enumerate()
    {
        let dedupe_key = format!("{source_scope}::{}", memory.key);
        if !seen.insert(dedupe_key) {
            continue;
        }
        hits.push(MemorySearchHit {
            key: memory.key,
            category: memory.category,
            content: memory.content,
            source_scope: source_scope.to_string(),
            match_reason: "fts_match".to_string(),
            score: memory_rank_score(source_scope, "fts_match", idx),
        });
    }

    for (idx, memory) in embedding::search_memories_vec(conn, query, category, limit)?
        .into_iter()
        .enumerate()
    {
        let dedupe_key = format!("{source_scope}::{}", memory.key);
        if !seen.insert(dedupe_key) {
            continue;
        }
        hits.push(MemorySearchHit {
            key: memory.key,
            category: memory.category,
            content: memory.content,
            source_scope: source_scope.to_string(),
            match_reason: "vector_match".to_string(),
            score: memory_rank_score(source_scope, "vector_match", idx),
        });
    }

    Ok(hits)
}

fn memory_rank_score(source_scope: &str, match_reason: &str, index: usize) -> i64 {
    let scope_bias = match source_scope {
        "workspace" => 2_000,
        "global" => 1_000,
        _ => 0,
    };
    let reason_bias = match match_reason {
        "fts_match" => 800,
        "vector_match" => 400,
        _ => 0,
    };
    scope_bias + reason_bias - index as i64
}

pub(crate) fn save_observation(
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

fn arg_str<'a>(args: &'a Value, key: &str) -> &'a str {
    args.get(key).and_then(Value::as_str).unwrap_or("")
}

fn promoted_file(category: &str) -> Option<&'static str> {
    match category {
        "decision" | "pattern" => Some("docs"),
        "skill" => Some("skills"),
        _ => None,
    }
}

fn git_text(workspace: impl AsRef<Path>, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(absolute_path(workspace))
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout)
        .ok()
        .map(|text| text.trim().to_string())
}

fn history_markdown_path(workspace: impl AsRef<Path>, target_filename: &str) -> PathBuf {
    workspace_history_dir(workspace).join(target_filename)
}

fn append_markdown_with_archive(
    workspace: impl AsRef<Path>,
    target_filename: &str,
    content: &str,
) -> Result<(), String> {
    let md_path = history_markdown_path(&workspace, target_filename);
    
    if md_path.exists() {
        if let Ok(metadata) = std::fs::metadata(&md_path) {
            if metadata.len() > 50 * 1024 {
                let archive_dir = workspace_history_dir(&workspace).join("archive");
                let _ = std::fs::create_dir_all(&archive_dir);
                let ts = now_unix();
                let archive_name = format!("{}_{}.md", target_filename.trim_end_matches(".md"), ts);
                let _ = std::fs::rename(&md_path, archive_dir.join(archive_name));
            }
        }
    }
    
    if let Some(parent) = md_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&md_path)
        .map_err(|e| e.to_string())?;
    
    file.write_all(content.as_bytes()).map_err(|e| e.to_string())?;
    Ok(())
}

fn append_promoted_memory_log(
    workspace: impl AsRef<Path>,
    category: &str,
    title: &str,
    content: &str,
) -> Result<(), String> {
    let target_file = match category {
        "decision" | "architecture" => "decisions.md",
        "pattern" | "convention" | "rule" | "protocol" => "patterns.md",
        _ => return Ok(()),
    };
    
    let date_str = std::process::Command::new("python")
        .args(&["-c", "import datetime; print(datetime.datetime.now().strftime('%Y-%m-%d'))"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown-date".to_string());
        
    let log_line = format!("\n### [{}] {}\n- **Category**: {}\n- **Content**: {}\n", date_str, title, category, content);
    append_markdown_with_archive(workspace, target_file, &log_line)
}

fn append_session_sync_markdown(
    workspace: impl AsRef<Path>,
    task_desc: &str,
    branch: &str,
    jira_issues: &[String],
    modified_files: &[String],
) -> Result<(), String> {
    let date_str = std::process::Command::new("python")
        .args(&["-c", "import datetime; print(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown-date".to_string());
        
    let log_line = format!(
        "\n- [CONFIRMED] **[SESSION_SYNC]** {} | Branch: {} | Issue: {:?}\n  - 📝 {}\n  - 📂 Modifies: {} files\n",
        date_str, branch, jira_issues, task_desc, modified_files.len()
    );
    append_markdown_with_archive(workspace, "inbox.md", &log_line)
}

fn update_memory_yaml_if_exists(workspace: impl AsRef<Path>, branch: &str) {
    let yaml_path = workspace_history_dir(&workspace).join("memory.yaml");
    if yaml_path.exists() {
        let script = format!(
r#"
import yaml, datetime, sys
path = r"{}"
try:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {{}}
    data["active_branch"] = r"{}"
    data["last_sync"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
except Exception as e:
    sys.exit(1)
"#, yaml_path.to_string_lossy().replace("\\", "\\\\"), branch);
        let _ = std::process::Command::new("python").args(&["-c", &script]).status();
    }
}
