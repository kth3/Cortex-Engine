//! scan_files — Python `cortex/scanner/finder.py:scan_files` 대응.

use anyhow::Result;
use serde_yaml::Value;
use std::collections::BTreeSet;
use std::path::Path;
use walkdir::WalkDir;

use crate::filters::{is_supported_extension, should_include};
use crate::ignores::{load_gitignore, should_ignore};
use crate::index_roots::{db_path_for_source_path, normalize_configured_index_roots, IndexRoot};
use crate::settings::load_settings;

/// 지능형 필터링을 적용해 인덱싱할 파일 상대 경로 목록 반환.
///
/// 출력은 정렬된 unique set (Python `sorted(list(set(files)))` 동등).
pub fn scan_files(workspace: &Path, settings_override: Option<Value>) -> Result<Vec<String>> {
    let workspace = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf());
    let settings = match settings_override {
        Some(s) => s,
        None => load_settings(&workspace)?,
    };

    let mut ignore_patterns = load_gitignore(&workspace);
    if let Some(extras) = settings
        .get("indexing_rules")
        .and_then(|r| r.get("exclude_paths"))
        .and_then(|v| v.as_sequence())
    {
        for v in extras {
            if let Some(s) = v.as_str() {
                let trimmed = s.trim_matches('/').to_string();
                if !trimmed.is_empty() {
                    ignore_patterns.push(trimmed);
                }
            }
        }
    }

    let mut files: BTreeSet<String> = BTreeSet::new();

    // 1. index_roots 스캔
    for index_root in normalize_configured_index_roots(&workspace, &settings) {
        collect_from_index_root(
            &workspace,
            &index_root,
            &ignore_patterns,
            &settings,
            &mut files,
        );
    }

    Ok(files.into_iter().collect())
}

fn collect_from_index_root(
    workspace: &Path,
    index_root: &IndexRoot,
    ignore_patterns: &[String],
    settings: &Value,
    out: &mut BTreeSet<String>,
) {
    let root_path = &index_root.source_path;
    if !root_path.exists() {
        return;
    }

    if root_path.is_file() {
        let ext = root_path
            .extension()
            .and_then(|s| s.to_str())
            .map(|s| format!(".{}", s));
        if let Some(e) = ext {
            if is_supported_extension(&e) {
                if let Some(db_path) = db_path_for_source_path(workspace, settings, root_path) {
                    if !should_ignore(&db_path, ignore_patterns)
                        && should_include(&db_path, settings)
                    {
                        out.insert(db_path);
                    }
                }
            }
        }
        return;
    }

    let walker = WalkDir::new(root_path).into_iter().filter_entry(|entry| {
        if let Some(db_path) = db_path_for_source_path(workspace, settings, entry.path()) {
            !should_ignore(&db_path, ignore_patterns)
        } else {
            true
        }
    });

    for entry in walker.filter_map(|e| e.ok()) {
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        let ext = match path.extension().and_then(|s| s.to_str()) {
            Some(s) => format!(".{}", s),
            None => continue,
        };
        if !is_supported_extension(&ext) {
            continue;
        }
        let db_path = match db_path_for_source_path(workspace, settings, path) {
            Some(s) => s,
            None => continue,
        };
        if should_ignore(&db_path, ignore_patterns) {
            continue;
        }

        if should_include(&db_path, settings) {
            out.insert(db_path);
        }
    }
}
