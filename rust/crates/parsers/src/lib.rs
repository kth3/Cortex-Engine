//! Cortex 파서 — Python `cortex/parsers/` 대응.
//!
//! tree-sitter 기반 코드 파서 + 텍스트 청킹(Markdown/HTML/CSS/PDF).
//! 출력 스키마는 Python 파서와 정확히 일치 (nodes/edges JSON).

pub mod common;

pub use common::{
    truncate, unresolved_fqn, unresolved_name, uuid5_for, EdgeRecord, NodeRecord, ParseResult,
    NAMESPACE_URL, UNRESOLVED_FQN_PREFIX, UNRESOLVED_NAME_PREFIX,
};
