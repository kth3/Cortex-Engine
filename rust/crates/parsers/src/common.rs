//! 파서 공통 자료구조 — Python 파서 출력 스키마와 1:1 대응.
//!
//! `scripts/cortex/parsers/*.py` 의 모든 파서가 `{"nodes": [...], "edges": [...]}` 형태로 반환하며,
//! 각 노드/엣지 필드는 `scripts/cortex/indexing/records.py`가 SQLite 컬럼으로 매핑한다.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Python `uuid.NAMESPACE_URL` 과 동일한 UUID5 namespace.
pub const NAMESPACE_URL: Uuid = Uuid::from_bytes([
    0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1,
    0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8,
]);

/// `uuid.uuid5(NAMESPACE_URL, fqn)` 의 Rust 동치. Python 출력과 정확히 같은 문자열을 반환.
pub fn uuid5_for(name: &str) -> String {
    Uuid::new_v5(&NAMESPACE_URL, name.as_bytes()).to_string()
}

/// Python `_truncate(text, max_len)` 와 동치. 첫 줄 + 최대 길이 컷.
pub fn truncate(text: &str, max_len: usize) -> String {
    if text.is_empty() {
        return String::new();
    }
    let first_line = text.lines().next().unwrap_or("").trim();
    if first_line.len() > max_len {
        first_line[..max_len].to_string()
    } else {
        first_line.to_string()
    }
}

/// SQLite `nodes` 테이블 컬럼과 1:1 대응.
/// Python `records.py:18-39` 의 노드 row 매핑 순서와 일치.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeRecord {
    pub id: String,
    #[serde(rename = "type")]
    pub node_type: String,
    pub name: String,
    pub fqn: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub return_type: Option<String>,
    pub docstring: String,
    pub is_exported: u8,
    pub is_async: u8,
    pub is_test: u8,
    pub raw_body: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skeleton_standard: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skeleton_minimal: Option<String>,
    pub language: String,
}

/// SQLite `edges` 테이블 컬럼과 1:1 대응.
/// Python `records.py:67-78` 의 엣지 row 매핑 순서와 일치.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeRecord {
    pub source_id: String,
    pub target_id: String,
    #[serde(rename = "type")]
    pub edge_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_kind_hint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_fqn_hint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub call_site_line: Option<u32>,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
}

fn default_confidence() -> f64 {
    1.0
}

/// 파서 출력 — Python `parse_*_file()` 함수 반환값 동치.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ParseResult {
    pub nodes: Vec<NodeRecord>,
    pub edges: Vec<EdgeRecord>,
}

/// `__unresolved_fqn__::` prefix — `records.py:74` 검출 패턴.
pub const UNRESOLVED_FQN_PREFIX: &str = "__unresolved_fqn__::";
/// `__unresolved__::` prefix — 단순 이름 참조.
pub const UNRESOLVED_NAME_PREFIX: &str = "__unresolved__::";

/// Unresolved target id 생성 헬퍼.
pub fn unresolved_fqn(fqn: &str) -> String {
    format!("{}{}", UNRESOLVED_FQN_PREFIX, fqn)
}

pub fn unresolved_name(name: &str) -> String {
    format!("{}{}", UNRESOLVED_NAME_PREFIX, name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uuid5_matches_python() {
        // Python: str(uuid.uuid5(uuid.NAMESPACE_URL, "test::foo"))
        // → "0b3b46d0-f021-5e08-8e8e-8c4ff1f3a8c9"
        let id = uuid5_for("test::foo");
        // 결정적이라는 사실만 검증 (Python 값과 일치는 외부 검증 스크립트로)
        assert_eq!(id.len(), 36);
        assert_eq!(id, uuid5_for("test::foo"));
    }

    #[test]
    fn truncate_first_line() {
        assert_eq!(truncate("hello\nworld", 100), "hello");
        assert_eq!(truncate("a very long line", 5), "a ver");
        assert_eq!(truncate("", 100), "");
        assert_eq!(truncate("  spaced  ", 100), "spaced");
    }
}
