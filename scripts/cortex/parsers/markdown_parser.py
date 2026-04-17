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

def parse_markdown_file(file_path: str, source: str) -> dict:
    """
    마크다운 파일(특히 스킬 문서)의 메타데이터와 본문을 단일 DB 노드로 변환합니다.
    """
    nodes = []
    
    # 기본값 설정
    skill_name = ""
    description = ""
    start_line = 1
    end_line = source.count('\n') + 1

    # 경로에서 스킬 이름 유추 (예: .../skills/my-skill/SKILL.md -> my-skill)
    # 부모 디렉토리 이름 사용 우선
    parts = file_path.replace('\\', '/').split('/')
    if len(parts) >= 2 and (parts[-1] == 'SKILL.md' or parts[-1] == 'README.md'):
        skill_name = parts[-2]
    else:
        skill_name = os.path.splitext(os.path.basename(file_path))[0]

    # 본문 전체를 하나의 노드로 간주 (Skill 타입)
    nodes.append({
        "id": f"skill::{file_path}",
        "type": "Skill",
        "name": skill_name,
        "fqn": skill_name,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "language": "markdown",
        "raw_body": source,
        "skeleton_standard": source[:500] + ("..." if len(source) > 500 else ""),
        "skeleton_minimal": skill_name
    })

    return {"nodes": nodes, "edges": []}
