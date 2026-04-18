import re
import os

# ==============================================================================
# 지원 확장자 메타데이터
# ==============================================================================
SUPPORTED_EXTENSIONS = {
    ".md": ("markdown", lambda file_path, source: parse_markdown_file(file_path, source)),
    ".html": ("html", lambda file_path, source: parse_markdown_file(file_path, source)),
    ".css": ("css", lambda file_path, source: parse_markdown_file(file_path, source))
}

# ==============================================================================
# 의미 기반 청킹 (Semantic Chunking with Overlap)
# ==============================================================================

def _advanced_semantic_chunking(text: str, max_len: int = 2500, overlap: int = 400) -> list[str]:
    """문단(\\n\\n) 기준으로 텍스트를 의미 단위로 분할하되,
    청크 간 overlap 글자수만큼 겹침(Overlap)을 두어 문맥 유실을 방지합니다.

    Args:
        text: 원본 텍스트
        max_len: 청크 최대 길이 (자)
        overlap: 청크 간 겹침 길이 (자)
    Returns:
        분할된 청크 문자열 리스트
    """
    if not text or not text.strip():
        return [text] if text else [""]

    # 전체 텍스트가 max_len 이내이면 분할하지 않음
    if len(text) <= max_len:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue

        # 현재 청크 + 새 문단이 max_len 이내이면 합산
        candidate = (current_chunk + "\n\n" + para_stripped) if current_chunk else para_stripped
        if len(candidate) <= max_len:
            current_chunk = candidate
        else:
            # 현재 청크가 비어있지 않으면 확정
            if current_chunk.strip():
                chunks.append(current_chunk)

            # 오버랩 텍스트 생성: 이전 청크 마지막 overlap 글자
            overlap_text = ""
            if chunks:
                tail = chunks[-1][-overlap:]
                # 단어 중간 잘림 방지: 마침표(.) 또는 줄바꿈(\n) 이후부터 시작
                cut_pos = -1
                for marker in [".", "\n"]:
                    pos = tail.find(marker)
                    if pos != -1:
                        if cut_pos == -1 or pos < cut_pos:
                            cut_pos = pos
                if cut_pos != -1:
                    overlap_text = tail[cut_pos + 1:].strip()
                else:
                    # 마침표/줄바꿈이 없으면 첫 공백 이후부터 시작 (단어 보존)
                    space_pos = tail.find(" ")
                    if space_pos != -1:
                        overlap_text = tail[space_pos + 1:].strip()
                    else:
                        overlap_text = tail.strip()

            # 새 청크를 오버랩 + 현재 문단으로 시작
            if overlap_text:
                current_chunk = overlap_text + "\n\n" + para_stripped
            else:
                current_chunk = para_stripped

            # 단일 문단 자체가 max_len 초과이면 강제 분할
            # 마커 확장: CSS(}; ) / HTML(>) 대응으로 minified 파일 CPU 폭발 방지
            _split_markers = [".", "\n", ">", "}", ";"]
            while len(current_chunk) > max_len:
                # max_len 지점에서 가장 가까운 분할 마커를 찾아 자름
                split_at = max_len
                for marker in _split_markers:
                    pos = current_chunk.rfind(marker, 0, max_len)
                    if pos != -1 and pos > max_len // 2:
                        split_at = pos + 1
                        break

                chunks.append(current_chunk[:split_at])

                # 강제 분할된 경우에도 오버랩 적용
                remainder = current_chunk[split_at:]
                tail_for_overlap = current_chunk[max(0, split_at - overlap):split_at]
                cut_pos = -1
                for marker in _split_markers:
                    pos = tail_for_overlap.find(marker)
                    if pos != -1:
                        if cut_pos == -1 or pos < cut_pos:
                            cut_pos = pos
                if cut_pos != -1:
                    glue = tail_for_overlap[cut_pos + 1:].strip()
                else:
                    space_pos = tail_for_overlap.find(" ")
                    glue = tail_for_overlap[space_pos + 1:].strip() if space_pos != -1 else tail_for_overlap.strip()

                current_chunk = (glue + "\n\n" + remainder).strip() if glue else remainder.strip()

    # 마지막 청크 처리
    if current_chunk.strip():
        chunks.append(current_chunk)

    return chunks if chunks else [text]


def parse_markdown_file(file_path: str, source: str) -> dict:
    """
    마크다운 파일을 의미 기반 오버랩 청킹하여 복수의 DB 노드로 변환합니다.
    긴 문서의 후반부 내용이 날아가지 않도록 문단 단위로 분할합니다.
    """
    nodes = []

    # 경로에서 스킬/문서 이름 유추
    parts = file_path.replace('\\', '/').split('/')
    if len(parts) >= 2 and (parts[-1] == 'SKILL.md' or parts[-1] == 'README.md'):
        skill_name = parts[-2]
    else:
        skill_name = os.path.splitext(os.path.basename(file_path))[0]

    total_end_line = source.count('\n') + 1

    # 소스를 의미 기반 오버랩 청크로 분할
    chunks = _advanced_semantic_chunking(source)

    current_offset = 0  # 검색 시작 위치를 추적하여 O(N) 성능 및 정확도 보장

    # 각 청크를 별도의 DB 노드로 등록
    for idx, chunk in enumerate(chunks):
        chunk_id = f"skill::{file_path}::chunk_{idx}"
        chunk_name = f"{skill_name} (Part {idx + 1})"
        chunk_fqn = f"{skill_name}::chunk_{idx}"

        # 청크 라인 위치 계산 (offset 추적 방식 — O(N) 보장)
        # 오버랩 부분을 피해 고유할 확률이 높은 중간 부분으로 검색
        search_target = chunk[100:150] if len(chunk) > 150 else chunk[:50]

        # 이전 청크가 끝난 지점 부근부터 검색 시작
        found_idx = source.find(search_target, max(0, current_offset - 500))

        if found_idx != -1:
            preceding_text = source[:found_idx]
            start_line = preceding_text.count('\n') + 1
            current_offset = found_idx + len(chunk) // 2  # 다음 검색을 위해 오프셋 전진
        else:
            # 안전장치: 찾지 못할 경우 이전 위치 기반 추산
            start_line = 1 if idx == 0 else min(nodes[-1]["end_line"], total_end_line)

        end_line = min(start_line + chunk.count('\n'), total_end_line)

        nodes.append({
            "id": chunk_id,
            "type": "Skill",
            "name": chunk_name,
            "fqn": chunk_fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "language": "markdown",
            "raw_body": chunk,
            "skeleton_standard": chunk[:500] + ("..." if len(chunk) > 500 else ""),
            "skeleton_minimal": f"{skill_name} part {idx + 1}"
        })

    return {"nodes": nodes, "edges": []}
