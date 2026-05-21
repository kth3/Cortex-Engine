//! scan_files — Python `cortex/scanner/finder.py:scan_files` 대응.

use anyhow::Result;
use serde_yaml::Value;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use crate::filters::{is_supported_extension, should_include};
use crate::ignores::{load_gitignore, should_ignore};
use crate::index_roots::{normalize_configured_index_roots, IndexRoot};
use crate::settings::load_settings;

/// 지능형 필터링을 적용해 인덱싱할 파일 상대 경로 목록 반환.
///
/// 출력은 정렬된 unique set (Python `sorted(list(set(files)))` 동등).
pub fn scan_files(workspace: &Path, settings_override: Option<Value>) -> Result<Vec<String>> {
    let workspace = workspace.canonicalize().unwrap_or_else(|_| workspace.to_path_buf());
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
        collect_from_index_root(&workspace, &index_root, &ignore_patterns, &settings, &mut files);
    }

    // 2. Cortex home 강제 포함 (rules/, docs/ 내 .md)
    let cortex_home = resolve_cortex_home(&workspace);
    let home_rel = relative_to(&workspace, &cortex_home);
    for sub in &["rules", "docs"] {
        let dir = cortex_home.join(sub);
        if dir.exists() {
            for entry in WalkDir::new(&dir).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file()
                    && entry.path().extension().and_then(|s| s.to_str()) == Some("md")
                {
                    if let Some(rel) = workspace_relative(&workspace, entry.path()) {
                        let _ = home_rel; // home_rel은 metadev 시 사용 가능
                        files.insert(rel);
                    }
                }
            }
        }
    }

    // 3. Cortex 엔진 스크립트 강제 포함 (.py)
    let scripts_dir = cortex_home.join("scripts");
    if scripts_dir.exists() {
        for entry in WalkDir::new(&scripts_dir).into_iter().filter_map(|e| e.ok()) {
            if !entry.file_type().is_file() {
                continue;
            }
            let p = entry.path();
            if p.extension().and_then(|s| s.to_str()) != Some("py") {
                continue;
            }
            let s = p.to_string_lossy();
            if s.contains("__pycache__") || s.contains(".venv") || s.contains("site-packages") {
                continue;
            }
            if let Some(rel) = workspace_relative(&workspace, p) {
                files.insert(rel);
            }
        }
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
        let ext = root_path.extension().and_then(|s| s.to_str()).map(|s| format!(".{}", s));
        if let Some(e) = ext {
            if is_supported_extension(&e) {
                if let Some(rel) = workspace_relative(workspace, root_path) {
                    if !should_ignore(&rel, ignore_patterns) && should_include(&rel, settings) {
                        out.insert(rel);
                    }
                }
            }
        }
        return;
    }

    let walker = WalkDir::new(root_path).into_iter().filter_entry(|entry| {
        if let Some(rel) = workspace_relative(workspace, entry.path()) {
            !should_ignore(&rel, ignore_patterns)
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
        let full_rel = match workspace_relative(workspace, path) {
            Some(s) => s,
            None => continue,
        };
        if should_ignore(&full_rel, ignore_patterns) {
            continue;
        }

        // DB 저장 경로 결정: index_root.db_root 기준 상대 경로 + 접두사
        let db_path = if index_root.db_root == "." {
            full_rel.clone()
        } else {
            // 워크스페이스 기준 절대 경로 → index_root.source_path 기준 상대
            match path.strip_prefix(root_path) {
                Ok(rel) => {
                    let rel_str = rel.to_string_lossy().replace('\\', "/");
                    format!("{}/{}", index_root.db_root, rel_str)
                }
                Err(_) => full_rel.clone(),
            }
        };

        if should_include(&full_rel, settings) {
            out.insert(db_path);
        }
    }
}

fn workspace_relative(workspace: &Path, path: &Path) -> Option<String> {
    let p = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    p.strip_prefix(workspace)
        .ok()
        .map(|r| r.to_string_lossy().replace('\\', "/"))
}

fn relative_to(base: &Path, target: &Path) -> String {
    target.strip_prefix(base)
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_else(|_| target.to_string_lossy().to_string())
}

/// Python `paths.py:resolve_cortex_home()` 동등.
/// CORTEX_HOME 환경변수가 우선, 없으면 워크스페이스 내 `.cortex` 디렉토리.
fn resolve_cortex_home(workspace: &Path) -> PathBuf {
    if let Ok(env) = std::env::var("CORTEX_HOME") {
        let p = PathBuf::from(env);
        if p.exists() {
            return p;
        }
    }
    let candidate = workspace.join(".cortex");
    if candidate.exists() {
        return candidate;
    }
    workspace.to_path_buf()
}
