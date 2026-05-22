//! 의미 기반 청킹 알고리즘 — Python `markdown_parser.py` 및 `pdf_parser.py` 의 `_advanced_semantic_chunking` 동치.
//!
//! 두 파서가 마커 구성만 다른 같은 알고리즘을 쓰므로 `ChunkConfig`로 파라미터화.

/// 청킹 동작 설정.
///
/// - markdown: overlap 마커 `['.', '\n']` + space 폴백, 강제분할 `['.', '\n', '>', '}', ';']` + space 폴백
/// - pdf:      overlap 마커 `['.', '\n', ' ']`, 강제분할 동일 (폴백 없음)
#[derive(Debug, Clone)]
pub struct ChunkConfig {
    pub max_len: usize,
    pub overlap: usize,
    /// 청크 사이 오버랩 추출 시 우선 마커
    pub overlap_primary: Vec<char>,
    /// overlap_primary 미발견 시 폴백 (보통 [' '])
    pub overlap_fallback: Vec<char>,
    /// 강제 분할 + 강제 분할 오버랩 마커 (Python `_split_markers`)
    pub split_markers: Vec<char>,
    /// 강제 분할 오버랩에서 split_markers 미발견 시 폴백
    pub split_fallback: Vec<char>,
}

impl ChunkConfig {
    pub fn markdown() -> Self {
        Self {
            max_len: 2500,
            overlap: 400,
            overlap_primary: vec!['.', '\n'],
            overlap_fallback: vec![' '],
            split_markers: vec!['.', '\n', '>', '}', ';'],
            split_fallback: vec![' '],
        }
    }

    pub fn pdf() -> Self {
        Self {
            max_len: 2500,
            overlap: 400,
            overlap_primary: vec!['.', '\n', ' '],
            overlap_fallback: vec![],
            split_markers: vec!['.', '\n', ' '],
            split_fallback: vec![],
        }
    }
}

/// Markdown 기본 청킹 (기존 호환).
pub fn semantic_chunk(text: &str) -> Vec<String> {
    semantic_chunk_config(text, &ChunkConfig::markdown())
}

pub fn semantic_chunk_with(text: &str, max_len: usize, overlap: usize) -> Vec<String> {
    let mut cfg = ChunkConfig::markdown();
    cfg.max_len = max_len;
    cfg.overlap = overlap;
    semantic_chunk_config(text, &cfg)
}

/// 핵심 청킹 알고리즘. 모든 인덱스는 char(코드 포인트) 기준.
pub fn semantic_chunk_config(text: &str, cfg: &ChunkConfig) -> Vec<String> {
    if text.is_empty() || text.trim().is_empty() {
        return if text.is_empty() {
            vec![String::new()]
        } else {
            vec![text.to_string()]
        };
    }

    let total_chars: Vec<char> = text.chars().collect();
    if total_chars.len() <= cfg.max_len {
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

        let separator_len = if current.is_empty() { 0 } else { 2 };
        let candidate_len = current_char_len + separator_len + para_char_len;

        if candidate_len <= cfg.max_len {
            if current.is_empty() {
                current = para_stripped.to_string();
            } else {
                current.push_str("\n\n");
                current.push_str(para_stripped);
            }
            current_char_len = candidate_len;
        } else {
            if !current.trim().is_empty() {
                chunks.push(current.clone());
            }

            // 청크 간 오버랩
            let overlap_text = if let Some(prev) = chunks.last() {
                let prev_chars: Vec<char> = prev.chars().collect();
                let tail_start = prev_chars.len().saturating_sub(cfg.overlap);
                let tail: String = prev_chars[tail_start..].iter().collect();
                find_with_fallback(&tail, &cfg.overlap_primary, &cfg.overlap_fallback)
            } else {
                String::new()
            };

            current = if !overlap_text.is_empty() {
                format!("{}\n\n{}", overlap_text, para_stripped)
            } else {
                para_stripped.to_string()
            };
            current_char_len = current.chars().count();

            // 단일 문단 max_len 초과 시 강제 분할
            force_split(&mut chunks, &mut current, &mut current_char_len, cfg);
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

/// 1차 markers의 leftmost 위치 + 1 이후 텍스트 반환. 없으면 fallback markers로 재시도.
/// 모두 없으면 빈 문자열 (또는 trim된 tail).
fn find_with_fallback(tail: &str, primary: &[char], fallback: &[char]) -> String {
    let chars: Vec<char> = tail.chars().collect();

    let find_leftmost = |markers: &[char]| -> Option<usize> {
        let mut best: Option<usize> = None;
        for (i, c) in chars.iter().enumerate() {
            if markers.contains(c) {
                best = Some(best.map_or(i, |p| p.min(i)));
            }
        }
        best
    };

    if let Some(p) = find_leftmost(primary) {
        let s: String = chars[p + 1..].iter().collect();
        return s.trim().to_string();
    }

    if !fallback.is_empty() {
        if let Some(p) = find_leftmost(fallback) {
            let s: String = chars[p + 1..].iter().collect();
            return s.trim().to_string();
        }
    }

    // 마커 미발견: markdown은 fallback ' '가 있어서 항상 fallback에서 잡힘
    // → 여기까지 오는 경우는 pdf(폴백 없음)에서 markers 모두 미발견 시.
    // Python pdf: `overlap_text = tail.strip()` → 전체 tail 사용.
    tail.trim().to_string()
}

fn force_split(
    chunks: &mut Vec<String>,
    current: &mut String,
    current_char_len: &mut usize,
    cfg: &ChunkConfig,
) {
    while *current_char_len > cfg.max_len {
        let chars: Vec<char> = current.chars().collect();

        let mut split_at = cfg.max_len;
        for marker in &cfg.split_markers {
            if let Some(pos) = chars[..cfg.max_len].iter().rposition(|c| c == marker) {
                if pos > cfg.max_len / 2 {
                    split_at = pos + 1;
                    break;
                }
            }
        }

        let first_chunk: String = chars[..split_at].iter().collect();
        chunks.push(first_chunk);

        let remainder: String = chars[split_at..].iter().collect();
        let overlap_start = split_at.saturating_sub(cfg.overlap);
        let tail_for_overlap: String = chars[overlap_start..split_at].iter().collect();
        let glue = find_with_fallback(&tail_for_overlap, &cfg.split_markers, &cfg.split_fallback);

        *current = if glue.is_empty() {
            remainder.trim().to_string()
        } else {
            format!("{}\n\n{}", glue, remainder).trim().to_string()
        };
        *current_char_len = current.chars().count();
    }
}

/// 멀티바이트 안전 char 슬라이싱 — Python `s[:n]` 동치.
pub fn char_slice_to(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_text_single_chunk() {
        assert_eq!(semantic_chunk("hello world"), vec!["hello world"]);
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

    #[test]
    fn pdf_config_uses_space_marker() {
        let cfg = ChunkConfig::pdf();
        assert!(cfg.overlap_primary.contains(&' '));
        assert!(cfg.split_markers.contains(&' '));
        assert!(cfg.overlap_fallback.is_empty());
    }
}
