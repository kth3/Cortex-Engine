use anyhow::{Context, Result};
use std::env;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use sha1::Digest as _;

pub(crate) const SQLITE_DB_FILENAME: &str = "memories.db";
pub(crate) const WORKSPACES_DIRNAME: &str = "workspaces";

pub(crate) fn read_text_source(file: &Path) -> Result<String> {
    let raw = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;
    Ok(raw.replace("\r\n", "\n"))
}

pub(crate) fn load_ignore_patterns(workspace: &Path, settings: &serde_yaml::Value) -> Vec<String> {
    let mut patterns = cortex_scanner::load_gitignore(workspace);
    if let Some(extras) = settings
        .get("indexing_rules")
        .and_then(|rules| rules.get("exclude_paths"))
        .and_then(|value| value.as_sequence())
    {
        for value in extras {
            if let Some(pattern) = value.as_str() {
                let trimmed = pattern.trim_matches('/').to_string();
                if !trimmed.is_empty() {
                    patterns.push(trimmed);
                }
            }
        }
    }
    patterns
}

pub(crate) fn should_track_path(
    rel_path: &str,
    settings: &serde_yaml::Value,
    ignore_patterns: &[String],
) -> bool {
    let ext = format!(".{}", file_extension(rel_path));
    cortex_scanner::is_supported_extension(&ext)
        && cortex_scanner::should_include(rel_path, settings)
        && !cortex_scanner::should_ignore(rel_path, ignore_patterns)
}

pub(crate) fn file_extension(path: &str) -> String {
    Path::new(path)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.to_lowercase())
        .unwrap_or_default()
}

pub(crate) fn vector_prefix_for_path(rel_path: &str) -> String {
    let parts: Vec<&str> = rel_path.split('/').collect();
    if parts.len() > 1 && !parts[0].starts_with('.') {
        parts[0].to_string()
    } else {
        "root".to_string()
    }
}

pub(crate) fn workspace_key(workspace: &Path) -> String {
    if let Ok(key) = env::var("CORTEX_WORKSPACE_KEY") {
        return key;
    }

    let text = workspace.to_string_lossy();
    let digest = sha1::Sha1::digest(text.as_bytes());
    let hex = format!("{:x}", digest);
    hex[..12].to_string()
}

pub(crate) fn workspace_id_for(workspace: &Path) -> String {
    let text = workspace.to_string_lossy();
    let digest = md5::compute(text.as_bytes());
    let hex = format!("{:x}", digest);
    hex[..8].to_string()
}

pub(crate) fn workspace_data_home() -> PathBuf {
    if let Some(path) = env::var_os("CORTEX_DATA_HOME") {
        return PathBuf::from(path);
    }

    home_dir().join(".cortex")
}

pub(crate) fn workspace_db_path(workspace: &Path) -> PathBuf {
    workspace_data_dir(workspace).join(SQLITE_DB_FILENAME)
}

pub(crate) fn workspace_data_dir(workspace: &Path) -> PathBuf {
    let dir = workspace_data_home()
        .join(WORKSPACES_DIRNAME)
        .join(workspace_key(workspace));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

pub(crate) fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub(crate) fn category_for(rel_path: &str) -> &'static str {
    if rel_path.contains("skills/") {
        "SKILL"
    } else if rel_path.starts_with(".cortex/") {
        "RULE"
    } else {
        "SOURCE"
    }
}

pub(crate) fn module_name_for(rel_path: &str, settings: &serde_yaml::Value) -> String {
    let norm_path = rel_path.replace('\\', "/");
    let modules = settings
        .get("indexing_rules")
        .and_then(|rules| rules.get("modules"))
        .and_then(|value| value.as_mapping());

    if let Some(modules) = modules {
        for (module_name, module_paths) in modules {
            let Some(module_name) = module_name.as_str() else {
                continue;
            };
            let Some(paths) = module_paths.as_sequence() else {
                continue;
            };

            for path in paths {
                let Some(path) = path.as_str() else {
                    continue;
                };
                let normalized = path.replace('\\', "/").trim_matches('/').to_string();
                if normalized.is_empty() {
                    continue;
                }
                if norm_path.starts_with(&format!("{}/", normalized))
                    || norm_path.ends_with(&normalized)
                {
                    return module_name.to_string();
                }
            }
        }
    }

    let parts: Vec<&str> = norm_path.split('/').collect();
    if parts.len() > 1 {
        parts[0].to_string()
    } else {
        "root".to_string()
    }
}

pub(crate) fn blake2b16_hex(bytes: &[u8]) -> String {
    use blake2::{
        digest::{Update, VariableOutput},
        Blake2bVar,
    };

    let mut hasher = Blake2bVar::new(16).expect("blake2b output size");
    hasher.update(bytes);
    let mut output = [0u8; 16];
    hasher
        .finalize_variable(&mut output)
        .expect("blake2b finalize");
    output.iter().map(|byte| format!("{:02x}", byte)).collect()
}

pub(crate) fn now_unix_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or_default()
}
