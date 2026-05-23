use std::fs;
use std::path::Path;
use std::process::Command;

use rusqlite::{params, OptionalExtension};
use serde_json::Value;

use crate::storage_tools::{open_connection, workspace_history_dir};

pub(crate) fn before_tool_call(tool_name: &str, args: &Value) -> Result<Option<String>, String> {
    let warning = keyword_warning(args);

    match tool_name {
        "replace_exact_text" => {
            let file_path = arg_str(args, "file_path");
            let old_content = arg_str(args, "old_content");
            let new_content = arg_str(args, "new_content");
            if file_path.is_empty() || old_content.is_empty() || new_content.is_empty() {
                return Err("Missing required parameters for replace_exact_text.".to_string());
            }
            if old_content.trim().len() < 5 {
                return Err("old_content is too short. Risk of ambiguous replacement.".to_string());
            }
        }
        "create_task_contract" => {
            if arg_str(args, "lane_id").is_empty() || arg_str(args, "task_name").is_empty() {
                return Err("Missing lane_id or task_name for create_task_contract.".to_string());
            }
        }
        _ => {}
    }

    Ok(warning)
}

pub(crate) fn after_edit(workspace: impl AsRef<Path>, file_path: &str) -> Result<Option<String>, String> {
    if !file_path.ends_with(".py") {
        return Ok(None);
    }

    let target = workspace.as_ref().join(file_path);
    if !target.exists() {
        return Ok(None);
    }

    let python = std::env::var("CORTEX_PYTHON_EXECUTABLE")
        .or_else(|_| std::env::var("CORTEX_PYTHON_FALLBACK"))
        .unwrap_or_else(|_| "python".to_string());
    let output = Command::new(python)
        .args(["-m", "py_compile"])
        .arg(&target)
        .output()
        .map_err(|err| err.to_string())?;

    if output.status.success() {
        return Ok(None);
    }

    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Ok(Some(format!(
        "[LINT ERROR] Python syntax error detected:\n{stderr}"
    )))
}

pub(crate) fn after_save_observation(workspace: impl AsRef<Path>) -> Result<usize, String> {
    let workspace = workspace.as_ref();
    let conn = open_connection(workspace)?;
    let last_id = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'last_extracted_obs_id'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|err| err.to_string())?
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or(0);

    let mut stmt = conn
        .prepare("SELECT id, type, content FROM observations WHERE id > ?1 ORDER BY id ASC")
        .map_err(|err| err.to_string())?;
    let rows = stmt
        .query_map(params![last_id], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })
        .map_err(|err| err.to_string())?;

    let mut observations = Vec::new();
    for row in rows {
        observations.push(row.map_err(|err| err.to_string())?);
    }
    if observations.is_empty() {
        return Ok(0);
    }

    let history_dir = workspace_history_dir(workspace);
    fs::create_dir_all(&history_dir).map_err(|err| err.to_string())?;
    let inbox = history_dir.join("inbox.md");
    let mut content = String::new();
    if inbox.exists() {
        content = fs::read_to_string(&inbox).map_err(|err| err.to_string())?;
    }
    for (_, obs_type, obs_content) in &observations {
        content.push_str(&format!(
            "- [PENDING] **[{}]** {}\n",
            obs_type.to_uppercase(),
            obs_content.replace('\n', " ")
        ));
    }
    fs::write(&inbox, content).map_err(|err| err.to_string())?;

    let max_id = observations
        .iter()
        .map(|(id, _, _)| *id)
        .max()
        .unwrap_or(last_id);
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_extracted_obs_id', ?1)",
        params![max_id.to_string()],
    )
    .map_err(|err| err.to_string())?;
    Ok(observations.len())
}

fn keyword_warning(args: &Value) -> Option<String> {
    let text = args.to_string().to_lowercase();
    let rules = [
        ("deslop", "rule::ai-slop-cleaner"),
        ("refactor", "rule::ai-slop-cleaner"),
        ("cleanup", "rule::ai-slop-cleaner"),
        ("deep dive", "protocol::deep-dive"),
        ("trace", "protocol::deep-dive"),
        ("why", "protocol::deep-dive"),
        ("계획", "protocol::progress-tracking"),
        ("진행", "protocol::progress-tracking"),
        ("추적", "protocol::progress-tracking"),
        ("plan", "protocol::progress-tracking"),
        ("track", "protocol::progress-tracking"),
    ];
    let mut matched = Vec::new();
    for (keyword, rule) in rules {
        if text.contains(keyword) && !matched.contains(&rule) {
            matched.push(rule);
        }
    }
    if matched.is_empty() {
        None
    } else {
        Some(format!(
            "Info: Keywords detected. You should check these rules: {}",
            matched.join(", ")
        ))
    }
}

fn arg_str<'a>(args: &'a Value, key: &str) -> &'a str {
    args.get(key).and_then(Value::as_str).unwrap_or("")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn before_tool_call_blocks_short_replace() {
        let err = before_tool_call(
            "replace_exact_text",
            &json!({"file_path": "a.py", "old_content": "x", "new_content": "changed"}),
        )
        .expect_err("short replace should fail");
        assert!(err.contains("old_content"));
    }

    #[test]
    fn before_tool_call_reports_keyword_warning() {
        let warning = before_tool_call("manage_todo", &json!({"task": "refactor plan"}))
            .expect("warning should not block")
            .expect("warning expected");
        assert!(warning.contains("rule::ai-slop-cleaner"));
        assert!(warning.contains("protocol::progress-tracking"));
    }
}
