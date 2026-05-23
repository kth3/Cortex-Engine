use anyhow::{anyhow, Result};
use serde_json::json;
use serde_yaml::{Mapping, Value};
use std::fs;
use std::path::Path;

pub(crate) fn cmd_index_roots_list(workspace: &Path) -> Result<()> {
    let settings = cortex_scanner::load_settings(workspace)?;
    let roots = cortex_scanner::normalize_configured_index_roots(workspace, &settings)
        .into_iter()
        .map(|root| {
            json!({
                "db_root": root.db_root,
                "source_path": root.source_path.to_string_lossy(),
                "external": root.external,
                "alias": root.alias,
            })
        })
        .collect::<Vec<_>>();
    println!("{}", serde_json::to_string_pretty(&json!({"index_roots": roots}))?);
    Ok(())
}

pub(crate) fn cmd_index_roots_count(workspace: &Path) -> Result<()> {
    let files = cortex_scanner::scan_files(workspace, None)?;
    println!("{}", serde_json::to_string_pretty(&json!({"scan_count": files.len()}))?);
    Ok(())
}

pub(crate) fn cmd_index_roots_add(
    workspace: &Path,
    path: &Path,
    alias: Option<&str>,
) -> Result<()> {
    let mut settings = read_local_settings(workspace)?;
    let mut roots = raw_roots(&settings);
    let entry = root_entry(workspace, path, alias)?;
    if !roots.contains(&entry) {
        roots.push(entry);
    }
    set_raw_roots(&mut settings, roots);
    write_local_settings(workspace, &settings)?;
    cmd_index_roots_list(workspace)
}

pub(crate) fn cmd_index_roots_remove(workspace: &Path, target: &str) -> Result<()> {
    let mut settings = read_local_settings(workspace)?;
    let mut roots = raw_roots(&settings);
    let before = roots.len();
    roots.retain(|root| !matches_target(root, target));
    if before == roots.len() {
        return Err(anyhow!("index root not found: {target}"));
    }
    set_raw_roots(&mut settings, roots);
    write_local_settings(workspace, &settings)?;
    cmd_index_roots_list(workspace)
}

fn root_entry(workspace: &Path, path: &Path, alias: Option<&str>) -> Result<Value> {
    let target = if path.is_absolute() {
        path.to_path_buf()
    } else {
        workspace.join(path)
    };
    let target = target.canonicalize().unwrap_or(target);
    let workspace = workspace.canonicalize().unwrap_or_else(|_| workspace.to_path_buf());
    if let Ok(rel) = target.strip_prefix(&workspace) {
        return Ok(Value::String(normalize_path(rel)));
    }

    let alias = alias
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| target.file_name().and_then(|name| name.to_str()).map(str::to_string))
        .ok_or_else(|| anyhow!("external index root requires an alias"))?;
    if alias.chars().any(|ch| "/\\:*?\"<>|".contains(ch)) {
        return Err(anyhow!("invalid external index root alias"));
    }

    let mut map = Mapping::new();
    map.insert(Value::String("path".to_string()), Value::String(normalize_path(&target)));
    map.insert(Value::String("external".to_string()), Value::Bool(true));
    map.insert(Value::String("alias".to_string()), Value::String(alias));
    Ok(Value::Mapping(map))
}

fn matches_target(root: &Value, target: &str) -> bool {
    let target = target.replace('\\', "/");
    match root {
        Value::String(path) => path.replace('\\', "/") == target,
        Value::Mapping(map) => {
            let path = map
                .get(&Value::String("path".to_string()))
                .and_then(Value::as_str)
                .map(|value| value.replace('\\', "/"));
            let alias = map
                .get(&Value::String("alias".to_string()))
                .and_then(Value::as_str)
                .map(str::to_string);
            let db_root = alias.as_ref().map(|value| format!("@external/{value}"));
            path.as_deref() == Some(&target)
                || alias.as_deref() == Some(&target)
                || db_root.as_deref() == Some(&target)
        }
        _ => false,
    }
}

fn read_local_settings(workspace: &Path) -> Result<Value> {
    let (_, local_path) = cortex_scanner::settings_paths(workspace);
    if !local_path.exists() {
        return Ok(Value::Mapping(Mapping::new()));
    }
    let text = fs::read_to_string(local_path)?;
    Ok(serde_yaml::from_str(&text)?)
}

fn write_local_settings(workspace: &Path, settings: &Value) -> Result<()> {
    let (_, local_path) = cortex_scanner::settings_paths(workspace);
    if let Some(parent) = local_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(local_path, serde_yaml::to_string(settings)?)?;
    Ok(())
}

fn raw_roots(settings: &Value) -> Vec<Value> {
    settings
        .get("indexing_rules")
        .and_then(|rules| rules.get("index_roots"))
        .map(|roots| match roots {
            Value::Sequence(items) => items.clone(),
            Value::Null => Vec::new(),
            other => vec![other.clone()],
        })
        .unwrap_or_default()
}

fn set_raw_roots(settings: &mut Value, roots: Vec<Value>) {
    if !matches!(settings, Value::Mapping(_)) {
        *settings = Value::Mapping(Mapping::new());
    }
    let map = settings.as_mapping_mut().expect("settings mapping");
    let key = Value::String("indexing_rules".to_string());
    if !matches!(map.get(&key), Some(Value::Mapping(_))) {
        map.insert(key.clone(), Value::Mapping(Mapping::new()));
    }
    let rules = map
        .get_mut(&key)
        .and_then(Value::as_mapping_mut)
        .expect("indexing rules mapping");
    rules.insert(
        Value::String("index_roots".to_string()),
        Value::Sequence(roots),
    );
}

fn normalize_path(path: &Path) -> String {
    let text = path.to_string_lossy().replace('\\', "/");
    if text.is_empty() {
        ".".to_string()
    } else {
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_external_alias_and_db_root() {
        let mut map = Mapping::new();
        map.insert(Value::String("path".to_string()), Value::String("../docs".to_string()));
        map.insert(Value::String("external".to_string()), Value::Bool(true));
        map.insert(Value::String("alias".to_string()), Value::String("Docs".to_string()));
        let root = Value::Mapping(map);
        assert!(matches_target(&root, "Docs"));
        assert!(matches_target(&root, "@external/Docs"));
    }
}
