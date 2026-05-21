//! index_roots 정규화 — Python `cortex/indexing/index_roots.py` 대응.
//!
//! 단순화된 포트: external root는 미지원, 기본 워크스페이스 루트만 처리.
//! 추후 외부 인덱싱 루트 지원 필요시 확장.

use serde_yaml::Value;
use std::path::{Path, PathBuf};

const DANGEROUS_PARTS: &[&str] = &[".git", "node_modules", "library", "temp"];

#[derive(Debug, Clone)]
pub struct IndexRoot {
    pub db_root: String,
    pub source_path: PathBuf,
}

/// settings.yaml 기준 인덱싱 루트 목록. 기본은 워크스페이스 루트(`.`).
pub fn normalize_configured_index_roots(workspace: &Path, settings: &Value) -> Vec<IndexRoot> {
    let ws = workspace.canonicalize().unwrap_or_else(|_| workspace.to_path_buf());
    let roots = effective_index_roots(settings);

    let mut out: Vec<IndexRoot> = Vec::new();
    let mut seen: Vec<String> = Vec::new();

    for root in roots {
        let raw_text = match root {
            Value::String(s) => s,
            // dict 형태(external)는 현재 미지원, skip
            _ => continue,
        };
        let trimmed = raw_text.trim();
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.contains('*') || trimmed.contains('?') {
            continue;
        }

        let target_raw = PathBuf::from(trimmed);
        let target = if target_raw.is_absolute() {
            target_raw
        } else {
            ws.join(&target_raw)
        };
        let target = target.canonicalize().unwrap_or(target);

        let db_root = match target.strip_prefix(&ws) {
            Ok(p) => {
                let s = p.to_string_lossy().replace('\\', "/");
                if s.is_empty() {
                    ".".to_string()
                } else {
                    s
                }
            }
            Err(_) => continue, // 워크스페이스 밖 (external 미지원)
        };

        // 위험 부분 체크
        let lower = db_root.to_lowercase();
        if db_root != "." && lower.split('/').any(|p| DANGEROUS_PARTS.contains(&p)) {
            continue;
        }

        if seen.contains(&db_root) {
            continue;
        }
        seen.push(db_root.clone());
        out.push(IndexRoot { db_root, source_path: target });
    }

    out
}

fn effective_index_roots(settings: &Value) -> Vec<Value> {
    let rules = settings.get("indexing_rules").unwrap_or(&Value::Null);
    let roots = rules.get("index_roots");
    match roots {
        None | Some(Value::Null) => vec![Value::String(".".to_string())],
        Some(Value::String(_)) | Some(Value::Mapping(_)) => vec![roots.unwrap().clone()],
        Some(Value::Sequence(list)) => {
            let mut unique: Vec<Value> = Vec::new();
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
