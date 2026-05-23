//! index_roots normalization and source/db path conversion.

use serde_yaml::Value;
use std::path::{Path, PathBuf};

const DANGEROUS_PARTS: &[&str] = &[".git", "node_modules", "library", "temp"];
const EXTERNAL_ROOT_PREFIX: &str = "@external";

#[derive(Debug, Clone)]
pub struct IndexRoot {
    pub db_root: String,
    pub source_path: PathBuf,
    pub external: bool,
    pub alias: Option<String>,
}

pub fn normalize_configured_index_roots(workspace: &Path, settings: &Value) -> Vec<IndexRoot> {
    let ws = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf());
    let roots = effective_index_roots(settings);

    let mut out = Vec::new();
    let mut seen = Vec::<String>::new();

    for root in roots {
        let Some(parsed) = parse_root(&ws, root) else {
            continue;
        };
        if seen.contains(&parsed.db_root) {
            continue;
        }
        seen.push(parsed.db_root.clone());
        out.push(parsed);
    }

    out
}

pub fn source_path_for_index_path(
    workspace: &Path,
    settings: &Value,
    db_path: &str,
) -> Option<PathBuf> {
    let db_text = db_path.replace('\\', "/");
    if !db_text.starts_with(&format!("{}/", EXTERNAL_ROOT_PREFIX)) {
        return Some(workspace.join(db_path));
    }

    for root in normalize_configured_index_roots(workspace, settings) {
        if !root.external {
            continue;
        }
        if db_text == root.db_root {
            return Some(root.source_path);
        }
        let prefix = format!("{}/", root.db_root);
        if let Some(rest) = db_text.strip_prefix(&prefix) {
            return Some(root.source_path.join(rest));
        }
    }
    None
}

pub fn db_path_for_source_path(
    workspace: &Path,
    settings: &Value,
    source_path: &Path,
) -> Option<String> {
    let source = source_path
        .canonicalize()
        .unwrap_or_else(|_| source_path.to_path_buf());

    for root in normalize_configured_index_roots(workspace, settings) {
        let root_path = root
            .source_path
            .canonicalize()
            .unwrap_or_else(|_| root.source_path.clone());
        let Ok(rel) = source.strip_prefix(&root_path) else {
            continue;
        };
        if root.db_root == "." {
            return Some(normalize_path_text(rel));
        }
        let rel_text = normalize_path_text(rel);
        return Some(if rel_text.is_empty() {
            root.db_root
        } else {
            format!("{}/{}", root.db_root, rel_text)
        });
    }
    None
}

fn parse_root(workspace: &Path, root: Value) -> Option<IndexRoot> {
    let (raw_text, external, alias) = match root {
        Value::String(s) => (s, false, None),
        Value::Mapping(map) => {
            let raw_text = map
                .get(&Value::String("path".to_string()))
                .and_then(Value::as_str)?
                .to_string();
            let external = map
                .get(&Value::String("external".to_string()))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let alias = map
                .get(&Value::String("alias".to_string()))
                .and_then(Value::as_str)
                .map(str::to_string);
            (raw_text, external, alias)
        }
        _ => return None,
    };

    let trimmed = raw_text.trim();
    if trimmed.is_empty() || trimmed.contains('*') || trimmed.contains('?') {
        return None;
    }

    let target_raw = PathBuf::from(trimmed);
    let target = if target_raw.is_absolute() {
        target_raw
    } else {
        workspace.join(target_raw)
    };
    let target = target.canonicalize().unwrap_or(target);

    let db_root = if external {
        let root_alias = normalize_alias(alias.as_deref(), &target)?;
        if has_dangerous_part(
            &target
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or_default()
                .to_lowercase(),
        ) {
            return None;
        }
        format!("{}/{}", EXTERNAL_ROOT_PREFIX, root_alias)
    } else {
        let rel = target.strip_prefix(workspace).ok()?;
        let text = normalize_path_text(rel);
        if text.is_empty() {
            ".".to_string()
        } else {
            text
        }
    };

    let lower = db_root.to_lowercase();
    if !external && db_root != "." && has_dangerous_part(&lower) {
        return None;
    }

    let final_alias = if external {
        Some(normalize_alias(alias.as_deref(), &target)?)
    } else {
        None
    };

    Some(IndexRoot {
        db_root,
        source_path: target,
        external,
        alias: final_alias,
    })
}

fn normalize_alias(raw_alias: Option<&str>, target: &Path) -> Option<String> {
    let alias = raw_alias
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| {
            target
                .file_name()
                .and_then(|s| s.to_str())
                .map(str::to_string)
        })?;
    if alias.chars().any(|ch| "/\\:*?\"<>|".contains(ch)) {
        return None;
    }
    Some(alias)
}

fn has_dangerous_part(path_text: &str) -> bool {
    path_text
        .split('/')
        .any(|part| DANGEROUS_PARTS.contains(&part))
}

fn normalize_path_text(path: &Path) -> String {
    let s = path.to_string_lossy().replace('\\', "/");
    if s == "." {
        String::new()
    } else {
        s
    }
}

fn effective_index_roots(settings: &Value) -> Vec<Value> {
    let rules = settings.get("indexing_rules").unwrap_or(&Value::Null);
    let roots = rules.get("index_roots");
    match roots {
        None | Some(Value::Null) => vec![Value::String(".".to_string())],
        Some(Value::String(_)) | Some(Value::Mapping(_)) => vec![roots.unwrap().clone()],
        Some(Value::Sequence(list)) => {
            let mut unique = Vec::new();
            for r in list {
                if !unique.contains(r) {
                    unique.push(r.clone());
                }
            }
            if unique.is_empty() {
                vec![Value::String(".".to_string())]
            } else {
                unique
            }
        }
        _ => vec![Value::String(".".to_string())],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_ID: AtomicU64 = AtomicU64::new(0);

    fn temp_dir(name: &str) -> PathBuf {
        let n = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "cortex_scanner_{}_{}_{}",
            name,
            std::process::id(),
            n
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("create temp dir");
        path
    }

    #[test]
    fn external_root_resolves_synthetic_path_to_source_path() {
        let workspace = temp_dir("workspace");
        let external = temp_dir("external").join("ExternalProject");
        fs::create_dir_all(external.join("Docs")).expect("create external docs");
        fs::write(external.join("Docs").join("note.md"), "note").expect("write note");
        let settings: Value = serde_yaml::from_str(&format!(
            "indexing_rules:\n  index_roots:\n    - path: {}\n      alias: ExternalProject\n      external: true\n",
            external.to_string_lossy().replace('\\', "/")
        ))
        .expect("parse settings");

        let source = source_path_for_index_path(
            &workspace,
            &settings,
            "@external/ExternalProject/Docs/note.md",
        )
        .expect("resolve source path");

        assert_eq!(
            source
                .canonicalize()
                .expect("canonical source")
                .to_string_lossy()
                .replace("\\\\?\\", ""),
            external
                .join("Docs")
                .join("note.md")
                .canonicalize()
                .expect("canonical expected")
                .to_string_lossy()
                .replace("\\\\?\\", "")
        );
    }

    #[test]
    fn external_source_path_maps_to_synthetic_path() {
        let workspace = temp_dir("workspace");
        let external = temp_dir("external").join("ExternalProject");
        fs::create_dir_all(external.join("Docs")).expect("create external docs");
        fs::write(external.join("Docs").join("note.md"), "note").expect("write note");
        let settings: Value = serde_yaml::from_str(&format!(
            "indexing_rules:\n  index_roots:\n    - path: {}\n      alias: ExternalProject\n      external: true\n",
            external.to_string_lossy().replace('\\', "/")
        ))
        .expect("parse settings");

        let db_path = db_path_for_source_path(
            &workspace,
            &settings,
            &external.join("Docs").join("note.md"),
        )
        .expect("resolve db path");

        assert_eq!(db_path, "@external/ExternalProject/Docs/note.md");
    }

    #[test]
    fn internal_source_path_maps_to_workspace_relative_path() {
        let workspace = temp_dir("workspace");
        fs::create_dir_all(workspace.join("src")).expect("create src");
        fs::write(workspace.join("src").join("main.py"), "print('ok')").expect("write source");
        let settings: Value = serde_yaml::from_str("indexing_rules:\n  index_roots:\n    - .\n")
            .expect("parse settings");

        let db_path = db_path_for_source_path(
            &workspace,
            &settings,
            &workspace.join("src").join("main.py"),
        )
        .expect("resolve db path");

        assert_eq!(db_path, "src/main.py");
    }
}
