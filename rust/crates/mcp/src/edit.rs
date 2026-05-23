use super::*;

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
    let inbox_items = crate::hooks::after_save_observation(&workspace)?;
    let hook_feedback = crate::hooks::after_edit(&workspace, file_path)?;
    Ok(json!({
        "success": true,
        "match_type": "exact",
        "hook_feedback": hook_feedback,
        "inbox_items": inbox_items,
    }))
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
    let digest = hasher.finalize();
    format!("{digest:x}")
}
