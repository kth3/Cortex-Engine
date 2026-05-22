//! Cortex 파서 — Python `cortex/parsers/` 대응.
//!
//! tree-sitter 기반 코드 파서 + 텍스트 청킹(Markdown/HTML/CSS/PDF).
//! 출력 스키마는 Python 파서와 정확히 일치 (nodes/edges JSON).

pub mod c_cpp;
pub mod chunking;
pub mod common;
pub mod java;
pub mod markdown;
pub mod pdf;

pub use c_cpp::parse_c_file;
pub use chunking::{semantic_chunk, semantic_chunk_config, semantic_chunk_with, ChunkConfig};
pub use common::{
    truncate, unresolved_fqn, unresolved_name, uuid5_for, EdgeRecord, NodeRecord, ParseResult,
    NAMESPACE_URL, UNRESOLVED_FQN_PREFIX, UNRESOLVED_NAME_PREFIX,
};
pub use java::parse_java_file;
pub use markdown::{parse_css_file, parse_html_file, parse_markdown_file};
pub use pdf::parse_pdf_file;
