//! Markdown/HTML/CSS 파서 — Python `parsers/markdown_parser.py` 대응.
//!
//! 텍스트 청킹 기반으로 청크당 1개 NodeRecord 생성.
//! ID 형식이 다른 파서와 다름: `skill::{file_path}::chunk_{idx}` (직접 문자열, UUID 아님).

use crate::chunking::{char_slice_to, semantic_chunk};
use crate::common::{NodeRecord, ParseResult};

/// Markdown 파일을 파싱하여 Skill 타입 청크 노드 리스트로 변환.
pub fn parse_markdown_file(file_path: &str, source: &str) -> ParseResult {
    parse_chunked(file_path, source, "markdown")
}

/// HTML/CSS도 동일 청킹 로직을 사용. Python `registry.py:33-34` 와 동치.
pub fn parse_html_file(file_path: &str, source: &str) -> ParseResult {
    // Python은 markdown_parser로 fallback하면서 language를 "html"로 지정하지 않고
    // type만 "Skill"로 유지. 단순화: 같은 함수 사용 + language 표시는 dispatch 시 처리.
    parse_chunked(file_path, source, "markdown")
}

pub fn parse_css_file(file_path: &str, source: &str) -> ParseResult {
    parse_chunked(file_path, source, "markdown")
}

fn parse_chunked(file_path: &str, source: &str, language: &str) -> ParseResult {
    let skill_name = derive_skill_name(file_path);
    let total_end_line = source.matches('\n').count() as u32 + 1;
    let chunks = semantic_chunk(source);

    let mut nodes: Vec<NodeRecord> = Vec::new();
    let mut current_offset: usize = 0;

    for (idx, chunk) in chunks.iter().enumerate() {
        let chunk_id = format!("skill::{}::chunk_{}", file_path, idx);
        let chunk_name = format!("{} (Part {})", skill_name, idx + 1);
        let chunk_fqn = format!("{}::chunk_{}", skill_name, idx);

        let chunk_char_len = chunk.chars().count();

        // 라인 추적 (Python offset 기반 검색과 동치)
        let search_target = pick_search_target(chunk);
        let search_from = current_offset.saturating_sub(500);

        let (start_line, found_idx) = match source
            .get(search_from..)
            .and_then(|s| s.find(search_target.as_str()))
        {
            Some(rel) => {
                let abs = search_from + rel;
                let preceding = &source[..abs];
                let sl = preceding.matches('\n').count() as u32 + 1;
                (sl, Some(abs))
            }
            None => {
                let sl = if idx == 0 {
                    1
                } else {
                    nodes
                        .last()
                        .map(|n| n.end_line.min(total_end_line))
                        .unwrap_or(1)
                };
                (sl, None)
            }
        };

        if let Some(abs) = found_idx {
            // Python `current_offset = found_idx + len(chunk) // 2`는 char 단위.
            // Rust는 byte offset 사용 — chunk 중간 char position의 byte index로 변환.
            let mid_chars = chunk_char_len / 2;
            let mid_byte = chunk
                .char_indices()
                .nth(mid_chars)
                .map(|(b, _)| b)
                .unwrap_or(chunk.len());
            current_offset = abs + mid_byte;
        }

        let end_line = (start_line + chunk.matches('\n').count() as u32).min(total_end_line);

        // Python `chunk[:500]`는 char 단위 슬라이싱 — 멀티바이트 안전.
        let skeleton_standard = if chunk_char_len > 500 {
            format!("{}...", char_slice_to(chunk, 500))
        } else {
            chunk.clone()
        };
        let skeleton_minimal = format!("{} part {}", skill_name, idx + 1);

        nodes.push(NodeRecord {
            id: chunk_id,
            node_type: "Skill".to_string(),
            name: chunk_name,
            fqn: chunk_fqn,
            file_path: file_path.to_string(),
            start_line,
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
            language: language.to_string(),
        });
    }

    ParseResult {
        nodes,
        edges: Vec::new(),
    }
}

/// 경로에서 스킬/문서 이름 유추. Python `markdown_parser.py:116-121` 와 동치:
/// - SKILL.md / README.md → 상위 디렉토리 이름
/// - 그 외 → 확장자 제외한 파일명
fn derive_skill_name(file_path: &str) -> String {
    let normalized = file_path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();

    if parts.len() >= 2 {
        let last = parts[parts.len() - 1];
        if last == "SKILL.md" || last == "README.md" {
            return parts[parts.len() - 2].to_string();
        }
    }

    let stem = parts.last().unwrap_or(&"");
    match stem.rfind('.') {
        Some(p) => stem[..p].to_string(),
        None => stem.to_string(),
    }
}

/// 검색 타겟: 청크의 100~150번째 char 구간 (오버랩 영역 피함).
/// 길이 부족 시 처음 50글자. Python `chunk[100:150] if len(chunk) > 150 else chunk[:50]` 동치.
fn pick_search_target(chunk: &str) -> String {
    let chars: Vec<char> = chunk.chars().collect();
    if chars.len() > 150 {
        chars[100..150].iter().collect()
    } else if chars.len() >= 50 {
        chars[..50].iter().collect()
    } else {
        chunk.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_name_from_skill_md() {
        assert_eq!(derive_skill_name("a/b/MySkill/SKILL.md"), "MySkill");
        assert_eq!(derive_skill_name("a/b/MySkill/README.md"), "MySkill");
    }

    #[test]
    fn skill_name_from_filename() {
        assert_eq!(derive_skill_name("notes/architecture.md"), "architecture");
        assert_eq!(derive_skill_name("plain.md"), "plain");
    }

    #[test]
    fn short_markdown_single_chunk() {
        let result = parse_markdown_file("notes/a.md", "# Title\n\nSome content");
        assert_eq!(result.nodes.len(), 1);
        assert_eq!(result.nodes[0].node_type, "Skill");
        assert_eq!(result.nodes[0].name, "a (Part 1)");
        assert_eq!(result.nodes[0].id, "skill::notes/a.md::chunk_0");
        assert_eq!(result.nodes[0].language, "markdown");
        assert_eq!(result.edges.len(), 0);
    }
}
