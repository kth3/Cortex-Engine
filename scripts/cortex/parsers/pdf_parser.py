"""
Cortex PDF Parser
pypdf를 이용해 PDF 파일의 텍스트를 추출하고 의미 기반으로 청킹하여 인덱싱합니다.
"""
import uuid
import os
import pypdf

# 지원 확장자 메타데이터
SUPPORTED_EXTENSIONS = {
    ".pdf": ("pdf", lambda file_path, _: parse_pdf_file(file_path))
}

def _advanced_semantic_chunking(text: str, max_len: int = 2500, overlap: int = 400) -> list[str]:
    """텍스트를 의미 단위로 분할하되 오버랩을 두어 문맥을 유지합니다."""
    if not text or not text.strip():
        return [text] if text else [""]

    if len(text) <= max_len:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    _split_markers = [".", "\n", " "]

    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue

        candidate = (current_chunk + "\n\n" + para_stripped) if current_chunk else para_stripped
        if len(candidate) <= max_len:
            current_chunk = candidate
        else:
            if current_chunk.strip():
                chunks.append(current_chunk)

            overlap_text = ""
            if chunks:
                tail = chunks[-1][-overlap:]
                cut_pos = -1
                for marker in _split_markers:
                    pos = tail.find(marker)
                    if pos != -1:
                        if cut_pos == -1 or pos < cut_pos:
                            cut_pos = pos
                if cut_pos != -1:
                    overlap_text = tail[cut_pos + 1:].strip()
                else:
                    overlap_text = tail.strip()

            if overlap_text:
                current_chunk = overlap_text + "\n\n" + para_stripped
            else:
                current_chunk = para_stripped

            while len(current_chunk) > max_len:
                split_at = max_len
                for marker in _split_markers:
                    pos = current_chunk.rfind(marker, 0, max_len)
                    if pos != -1 and pos > max_len // 2:
                        split_at = pos + 1
                        break

                chunks.append(current_chunk[:split_at])
                remainder = current_chunk[split_at:]
                
                # Apply overlap for forced split
                tail_for_overlap = current_chunk[max(0, split_at - overlap):split_at]
                cut_pos = -1
                for marker in _split_markers:
                    pos = tail_for_overlap.find(marker)
                    if pos != -1:
                        if cut_pos == -1 or pos < cut_pos:
                            cut_pos = pos
                
                glue = tail_for_overlap[cut_pos + 1:].strip() if cut_pos != -1 else tail_for_overlap.strip()
                current_chunk = (glue + "\n\n" + remainder).strip() if glue else remainder.strip()

    if current_chunk.strip():
        chunks.append(current_chunk)

    return chunks if chunks else [text]

def parse_pdf_file(file_path: str) -> dict:
    """PDF 파일 파싱 및 텍스트 추출/청킹 로직"""
    nodes = []
    
    # Cortex 환경의 절대 경로를 계산
    # file_path는 보통 워크스페이스 기준 상대 경로 (예: .agents/knowledge/examples/...)
    # parser 실행 시 워크스페이스 루트에서 실행된다고 가정
    abs_path = os.path.abspath(file_path)
    
    extracted_text = ""
    try:
        if os.path.exists(abs_path):
            with open(abs_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    extracted_text += page.extract_text() + "\n\n"
        else:
            return {"nodes": [], "edges": []}
    except Exception as e:
        print(f"Error reading PDF {file_path}: {e}")
        return {"nodes": [], "edges": []}
    
    if not extracted_text.strip():
        return {"nodes": [], "edges": []}
        
    doc_name = os.path.splitext(os.path.basename(file_path))[0]
    chunks = _advanced_semantic_chunking(extracted_text)
    
    for idx, chunk in enumerate(chunks):
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::chunk_{idx}"))
        chunk_name = f"{doc_name} (Part {idx + 1})"
        chunk_fqn = f"{doc_name}::chunk_{idx}"
        
        nodes.append({
            "id": chunk_id,
            "type": "Document",
            "name": chunk_name,
            "fqn": chunk_fqn,
            "file_path": file_path,
            "start_line": 1,
            "end_line": chunk.count('\n') + 1,
            "language": "pdf",
            "raw_body": chunk,
            "skeleton_standard": chunk[:500] + ("..." if len(chunk) > 500 else ""),
            "skeleton_minimal": f"PDF Chunk {idx + 1}"
        })
        
    return {"nodes": nodes, "edges": []}
