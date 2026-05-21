//! settings.yaml/settings.local.yaml 로드 + 병합 — Python `cortex/config/settings.py` 대응.

use anyhow::Result;
use serde_yaml::{Mapping, Value};
use std::fs;
use std::path::{Path, PathBuf};

/// Python `paths.py:settings_paths()` 동등.
///
/// Cortex 워크스페이스에서 settings 경로 두 개를 반환:
/// 1. .cortex/settings.yaml
/// 2. .cortex/settings.local.yaml
pub fn settings_paths(workspace: &Path) -> (PathBuf, PathBuf) {
    let cortex_dir = workspace.join(".cortex");
    (
        cortex_dir.join("settings.yaml"),
        cortex_dir.join("settings.local.yaml"),
    )
}

/// Python `load_settings()` 와 동일한 병합 로직:
/// - settings.yaml 기본
/// - settings.local.yaml 오버라이드 + 리스트는 dict.fromkeys 방식 dedup 병합
/// - indexing_rules.index_roots는 로컬 우선 (병합 대신 교체)
pub fn load_settings(workspace: &Path) -> Result<Value> {
    let (settings_path, local_path) = settings_paths(workspace);

    let mut settings = read_yaml(&settings_path).unwrap_or(Value::Mapping(Mapping::new()));

    if let Ok(local) = read_yaml(&local_path) {
        if let (Value::Mapping(ref mut base), Value::Mapping(local_map)) = (&mut settings, local) {
            merge_top_level(base, local_map);
        }
    }

    Ok(settings)
}

fn read_yaml(path: &Path) -> Result<Value> {
    let content = fs::read_to_string(path)?;
    let val: Value = serde_yaml::from_str(&content)?;
    Ok(val)
}

fn merge_top_level(base: &mut Mapping, local: Mapping) {
    for (k, v) in local {
        if k.as_str() == Some("indexing_rules") {
            if let Value::Mapping(local_rules) = v {
                if let Some(Value::Mapping(base_rules)) = base.get_mut(&k) {
                    merge_indexing_rules(base_rules, local_rules);
                } else {
                    base.insert(k, Value::Mapping(local_rules));
                }
                continue;
            }
            // local의 indexing_rules가 mapping이 아니면 통째로 교체
        }
        base.insert(k, v);
    }
}

fn merge_indexing_rules(base: &mut Mapping, local: Mapping) {
    for (k, v) in local {
        match k.as_str() {
            Some("index_roots") => {
                // 로컬이 항상 우선
                base.insert(k, v);
            }
            _ => {
                // 양쪽 다 리스트면 dedup 병합, 아니면 로컬로 교체
                if let (Some(Value::Sequence(base_list)), Value::Sequence(local_list)) =
                    (base.get(&k).cloned(), v.clone())
                {
                    let mut seen: Vec<Value> = Vec::new();
                    for item in base_list.into_iter().chain(local_list) {
                        if !seen.contains(&item) {
                            seen.push(item);
                        }
                    }
                    base.insert(k, Value::Sequence(seen));
                } else {
                    base.insert(k, v);
                }
            }
        }
    }
}
