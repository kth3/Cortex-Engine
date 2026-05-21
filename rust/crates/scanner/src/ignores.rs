//! Ignore 패턴 — Python `cortex/scanner/ignores.py` 대응.

use std::fs;
use std::path::Path;

/// Python `ignores.py:4-10`의 DEFAULT_IGNORES 와 정확히 일치.
pub const DEFAULT_IGNORES: &[&str] = &[
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", "build", ".gradle", ".idea", ".vscode",
    ".cortex", "target", ".next", "*.min.js", "*.min.css",
    "*.pyc", "*.class", "*.o", "*.obj", "*.exe", "*.out",
    "Library", "Temp", "Logs", "obj", // Unity 캐시
];

/// `.gitignore` 로드 후 DEFAULT_IGNORES와 병합.
pub fn load_gitignore(workspace: &Path) -> Vec<String> {
    let mut patterns: Vec<String> = DEFAULT_IGNORES.iter().map(|s| s.to_string()).collect();
    let gi_path = workspace.join(".gitignore");
    if let Ok(content) = fs::read_to_string(&gi_path) {
        for line in content.lines() {
            let line = line.trim();
            if !line.is_empty() && !line.starts_with('#') {
                patterns.push(line.trim_matches('/').to_string());
            }
        }
    }
    patterns
}

/// Python `ignores.py:should_ignore` 대응.
///
/// 1. rel_path의 각 path component를 패턴과 매칭
/// 2. 전체 rel_path도 패턴과 매칭
pub fn should_ignore(rel_path: &str, patterns: &[String]) -> bool {
    let rel = rel_path.replace('\\', "/");
    let parts: Vec<&str> = rel.split('/').collect();

    for part in &parts {
        for pattern in patterns {
            if fnmatch(part, pattern) {
                return true;
            }
        }
    }
    for pattern in patterns {
        if fnmatch(&rel, pattern) {
            return true;
        }
    }
    false
}

fn fnmatch(name: &str, pattern: &str) -> bool {
    globset::Glob::new(pattern)
        .ok()
        .map(|g| g.compile_matcher().is_match(name))
        .unwrap_or(false)
}
