//! 의미 기반 청킹 알고리즘 — Python `markdown_parser.py:_advanced_semantic_chunking` 동치.
//!
//! 문단(`\n\n`) 단위로 텍스트를 분할하고, 청크 간 오버랩(기본 400자)을 둠.
//! 단일 문단이 max_len을 초과하면 분할 마커(`.`, `\n`, `>`, `}`, `;`) 기준 강제 분할.
//!
//! **중요**: Python `len(str)`은 코드 포인트(char) 단위이므로 Rust도 동일하게 char 기준으로 동작해야 함.
//! 바이트 슬라이싱(`s[..n]`)은 멀티바이트 문자 경계에서 패닉 → 모두 `Vec<char>` 기반 인덱싱 사용.

const DEFAULT_MAX_LEN: usize = 2500;
const DEFAULT_OVERLAP: usize = 400;

/// 청크 분할 (Python `_advanced_semantic_chunking` 와 동치).
pub fn semantic_chunk(text: &str) -> Vec<String> {
    semantic_chunk_with(text, DEFAULT_MAX_LEN, DEFAULT_OVERLAP)
}

pub fn semantic_chunk_with(text: &str, max_len: usize, overlap: usize) -> Vec<String> {
    if text.is_empty() || text.trim().is_empty() {
        return if text.is_empty() { vec![String::new()] } else { vec![text.to_string()] };
    }

    // 전체 텍스트가 max_len 이내(char 단위)이면 분할하지 않음
    let total_chars: Vec<char> = text.chars().collect();
    if total_chars.len() <= max_len {
        return vec![text.to_string()];
    }

    let paragraphs: Vec<&str> = text.split("\n\n").collect();
    let mut chunks: Vec<String> = Vec::new();
    let mut current: String = String::new();
    let mut current_char_len: usize = 0;

    for para in &paragraphs {
        let para_stripped = para.trim();
        if para_stripped.is_empty() {
            continue;
        }

        let para_char_len = para_stripped.chars().count();

        // 현재 청크 + "\n\n" + 새 문단의 char 길이
        let separator_len = if current.is_empty() { 0 } else { 2 };
        let candidate_len = current_char_len + separator_len + para_char_len;

        if candidate_len <= max_len {
            if current.is_empty() {
                current = para_stripped.to_string();
            } else {
                current.push_str("\n\n");
                current.push_str(para_stripped);
            }
            current_char_len = candidate_len;
        } else {
            // 현재 청크 확정
            if !current.trim().is_empty() {
                chunks.push(current.clone());
            }

            // 오버랩: 이전 청크 끝부분의 overlap 글자 (단어 보존)
            let overlap_text = if let Some(prev) = chunks.last() {
                let prev_chars: Vec<char> = prev.chars().collect();
                let tail_start = prev_chars.len().saturating_sub(overlap);
                let tail: String = prev_chars[tail_start..].iter().collect();
                find_overlap_start(&tail, &['.', '\n'])
            } else {
                String::new()
            };

            // 새 청크 = 오버랩 + 현재 문단
            current = if !overlap_text.is_empty() {
                format!("{}\n\n{}", overlap_text, para_stripped)
            } else {
                para_stripped.to_string()
            };
            current_char_len = current.chars().count();

            // 단일 문단이 max_len 초과면 강제 분할
            force_split(&mut chunks, &mut current, &mut current_char_len, max_len, overlap);
        }
    }

    if !current.trim().is_empty() {
        chunks.push(current);
    }

    if chunks.is_empty() {
        vec![text.to_string()]
    } else {
        chunks
    }
}

/// 청크 끝부분에서 분할 마커 이후 텍스트를 추출 (단어 중간 잘림 방지).
/// Python 로직: 마커 발견 시 마커+1 위치부터, 없으면 첫 공백 이후.
fn find_overlap_start(tail: &str, markers: &[char]) -> String {
    let chars: Vec<char> = tail.chars().collect();

    let mut cut_pos: Option<usize> = None;
    for (i, c) in chars.iter().enumerate() {
        if markers.contains(c) {
            cut_pos = match cut_pos {
                Some(prev) if i < prev => Some(i),
                Some(prev) => Some(prev),
                None => Some(i),
            };
            // Python은 첫 매칭 마커의 첫 위치만 가져옴, break 안 함 — 모든 마커 중 가장 앞
        }
    }

    if let Some(p) = cut_pos {
        let s: String = chars[p + 1..].iter().collect();
        return s.trim().to_string();
    }

    // 공백 fallback
    if let Some(space) = chars.iter().position(|c| *c == ' ') {
        let s: String = chars[space + 1..].iter().collect();
        return s.trim().to_string();
    }

    tail.trim().to_string()
}

/// `current`가 `max_len`(char 단위)을 초과하면 분할 마커 기준 강제 분할.
fn force_split(
    chunks: &mut Vec<String>,
    current: &mut String,
    current_char_len: &mut usize,
    max_len: usize,
    overlap: usize,
) {
    let split_markers: [char; 5] = ['.', '\n', '>', '}', ';'];

    while *current_char_len > max_len {
        let chars: Vec<char> = current.chars().collect();

        // max_len 지점 왼쪽으로 마커 검색 (Python rfind 동치)
        let mut split_at = max_len;
        for marker in &split_markers {
            if let Some(pos) = chars[..max_len].iter().rposition(|c| c == marker) {
                if pos > max_len / 2 {
                    split_at = pos + 1;
                    break;
                }
            }
        }

        let first_chunk: String = chars[..split_at].iter().collect();
        chunks.push(first_chunk);

        let remainder: String = chars[split_at..].iter().collect();
        let overlap_start = split_at.saturating_sub(overlap);
        let tail_for_overlap: String = chars[overlap_start..split_at].iter().collect();
        let glue = find_overlap_start(&tail_for_overlap, &split_markers);

        *current = if glue.is_empty() {
            remainder.trim().to_string()
        } else {
            format!("{}\n\n{}", glue, remainder).trim().to_string()
        };
        *current_char_len = current.chars().count();
    }
}

/// 멀티바이트 안전 char 슬라이싱 헬퍼 — `s[..n]` 동치 (코드 포인트 기준).
pub fn char_slice_to(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_text_single_chunk() {
        let chunks = semantic_chunk("hello world");
        assert_eq!(chunks, vec!["hello world"]);
    }

    #[test]
    fn empty_text() {
        assert_eq!(semantic_chunk(""), vec![""]);
        assert_eq!(semantic_chunk("   "), vec!["   "]);
    }

    #[test]
    fn paragraph_split() {
        let big = "para1\n\n".to_string() + &"a".repeat(2500);
        let chunks = semantic_chunk(&big);
        assert!(chunks.len() >= 2);
    }

    #[test]
    fn korean_does_not_panic() {
        let text = "안녕하세요\n\n".repeat(500);
        let chunks = semantic_chunk(&text);
        assert!(!chunks.is_empty());
    }

    #[test]
    fn char_slice_safe_for_multibyte() {
        assert_eq!(char_slice_to("한국어 hello", 3), "한국어");
        assert_eq!(char_slice_to("hello", 100), "hello");
    }
}
