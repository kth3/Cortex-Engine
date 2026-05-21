//! 확장자/include 화이트리스트 — Python `cortex/scanner/filters.py` 대응.

use serde_yaml::Value;
use std::path::Path;

/// Python `parsers/registry.py:84`의 SUPPORTED_EXTENSIONS 와 정확히 일치.
pub const SUPPORTED_EXTENSIONS: &[&str] = &[
    ".c", ".cpp", ".h", ".hpp", // C/C++
    ".java",                    // Java
    ".md", ".html", ".css",     // Markdown/HTML/CSS
    ".pdf",                     // PDF
    ".py",                      // Python
    ".cs",                      // C#
    ".ts", ".tsx",              // TypeScript
];

pub fn is_supported_extension(ext: &str) -> bool {
    SUPPORTED_EXTENSIONS.iter().any(|e| e.eq_ignore_ascii_case(ext))
}

/// fnmatch 동등 매칭 (단순 wildcard `*`, `?`).
fn fnmatch(name: &str, pattern: &str) -> bool {
    // globset로 단일 패턴 매칭. 실패 시 false.
    globset::Glob::new(pattern)
        .ok()
        .and_then(|g| Some(g.compile_matcher().is_match(name)))
        .unwrap_or(false)
}

/// pathlib `Path(rel).match(pattern)` — `**` 포함 패턴 매칭.
fn path_match(rel: &str, pattern: &str) -> bool {
    if let Ok(glob) = globset::Glob::new(pattern) {
        glob.compile_matcher().is_match(rel)
    } else {
        false
    }
}

/// Python `scanner/filters.py:should_include` 대응.
///
/// Whitelist > include_paths > modules 순서로 평가.
pub fn should_include(rel_path: &str, settings: &Value) -> bool {
    let rules = settings.get("indexing_rules").unwrap_or(&Value::Null);
    let rel = rel_path.replace('\\', "/");
    let basename = Path::new(&rel)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("");

    let matches = |pattern: &str| -> bool {
        if pattern.contains("**") {
            path_match(&rel, pattern)
        } else {
            fnmatch(&rel, pattern) || fnmatch(basename, pattern)
        }
    };

    // 1. 화이트리스트
    if let Some(Value::Sequence(whitelist)) = rules.get("config_whitelist") {
        for v in whitelist {
            if let Some(p) = v.as_str() {
                if matches(p) {
                    return true;
                }
            }
        }
    }

    // 2. include_paths (기본 "**" — settings.yaml 없는 워크스페이스 전체 포함)
    let default_include = vec![Value::String("**".to_string())];
    let includes = rules
        .get("include_paths")
        .and_then(|v| v.as_sequence())
        .cloned()
        .unwrap_or(default_include);
    for v in &includes {
        if let Some(p) = v.as_str() {
            if matches(p) {
                return true;
            }
        }
    }

    // 3. modules 경로
    if let Some(Value::Mapping(modules)) = rules.get("modules") {
        for (_, mod_paths) in modules {
            if let Value::Sequence(paths) = mod_paths {
                for p in paths {
                    if let Some(s) = p.as_str() {
                        if rel.starts_with(s) || fnmatch(&rel, s) {
                            return true;
                        }
                    }
                }
            }
        }
    }

    false
}
