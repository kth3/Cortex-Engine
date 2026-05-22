//! PDF 파서 — Python `parsers/pdf_parser.py` 대응.
//!
//! pypdf로 페이지별 텍스트 추출 후 페이지 간 "\n\n" 구분자로 연결.
//! 이후 `semantic_chunk_config(text, ChunkConfig::pdf())` 로 의미 청킹.
//! 청크 ID는 UUID5 (`uuid.uuid5(NAMESPACE_URL, "{file_path}::chunk_{idx}")`).

use std::path::Path;

use crate::chunking::{char_slice_to, semantic_chunk_config, ChunkConfig};
use crate::common::{uuid5_for, NodeRecord, ParseResult};

/// PDF 파일을 파싱하여 Document 청크 노드 리스트로 변환.
///
/// 입력 `file_path`는 워크스페이스 기준 상대 경로(저장용). 실제 추출은 `abs_path`에서 수행.
pub fn parse_pdf_file(file_path: &str, abs_path: &Path) -> ParseResult {
    let extracted_text = match extract_pdf_text(abs_path) {
        Ok(t) => t,
        Err(_) => return ParseResult::default(),
    };

    if extracted_text.trim().is_empty() {
        return ParseResult::default();
    }

    let doc_name = derive_doc_name(file_path);
    let chunks = semantic_chunk_config(&extracted_text, &ChunkConfig::pdf());

    let mut nodes: Vec<NodeRecord> = Vec::new();
    for (idx, chunk) in chunks.iter().enumerate() {
        let chunk_id = uuid5_for(&format!("{}::chunk_{}", file_path, idx));
        let chunk_name = format!("{} (Part {})", doc_name, idx + 1);
        let chunk_fqn = format!("{}::chunk_{}", doc_name, idx);

        let chunk_char_len = chunk.chars().count();
        let skeleton_standard = if chunk_char_len > 500 {
            format!("{}...", char_slice_to(chunk, 500))
        } else {
            chunk.clone()
        };
        let skeleton_minimal = format!("PDF Chunk {}", idx + 1);

        let end_line = chunk.matches('\n').count() as u32 + 1;

        nodes.push(NodeRecord {
            id: chunk_id,
            node_type: "Document".to_string(),
            name: chunk_name,
            fqn: chunk_fqn,
            file_path: file_path.to_string(),
            start_line: 1,
            end_line,
            signature: None,
            return_type: None,
            docstring: None,
            is_exported: None,
            is_async: None,
            is_test: None,
            raw_body: chunk.clone(),
            skeleton_standard: Some(skeleton_standard),
            skeleton_minimal: Some(skeleton_minimal),
            language: "pdf".to_string(),
        });
    }

    ParseResult { nodes, edges: Vec::new() }
}

/// 페이지별 텍스트 추출 + "\n\n" 연결.
///
/// **주의**: `pdf-extract` crate는 전체 텍스트를 한 번에 반환하므로 페이지 경계가 사라진다.
/// Python `pypdf`는 페이지별로 분리되어 "\n\n"로 연결되는데, 이 차이로 청크 결과가 달라질 수 있다.
/// Phase 2e에서는 우선 pdf-extract로 진행하고, 검증 시 차이가 크면 lopdf로 페이지별 추출 전환.
fn extract_pdf_text(abs_path: &Path) -> anyhow::Result<String> {
    if !abs_path.exists() {
        anyhow::bail!("pdf not found: {:?}", abs_path);
    }
    let text = pdf_extract::extract_text(abs_path)?;
    // pypdf 호환: 페이지 끝마다 "\n\n" 가 들어가는데, pdf-extract는 페이지 사이 "\n"만 둠.
    // 임시 처방: 단일 "\n" 사이가 페이지 경계로 추정되는 경우는 보존(추후 lopdf 전환 시 정확화).
    Ok(format!("{}\n\n", text))
}

fn derive_doc_name(file_path: &str) -> String {
    let normalized = file_path.replace('\\', "/");
    let last = normalized.split('/').next_back().unwrap_or("");
    match last.rfind('.') {
        Some(p) => last[..p].to_string(),
        None => last.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn doc_name_extraction() {
        assert_eq!(derive_doc_name("docs/manual.pdf"), "manual");
        assert_eq!(derive_doc_name("paper.pdf"), "paper");
        assert_eq!(derive_doc_name("a/b/c/x.pdf"), "x");
    }

    #[test]
    fn missing_file_returns_empty() {
        let result = parse_pdf_file("nonexistent.pdf", Path::new("/tmp/nonexistent.pdf"));
        assert_eq!(result.nodes.len(), 0);
        assert_eq!(result.edges.len(), 0);
    }
}
