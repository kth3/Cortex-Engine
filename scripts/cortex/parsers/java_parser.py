"""
Cortex Java Parser
정규식 기반으로 Java 소스를 노드(클래스/메서드/인터페이스)로 변환
외부 라이브러리 없이 표준 라이브러리만 사용
"""
import re
import uuid

# ==============================================================================
# 지원 확장자 메타데이터
# ==============================================================================
SUPPORTED_EXTENSIONS = {
    ".java": ("java", lambda file_path, source: parse_java_file(file_path, source))
}

# ==============================================================================
# 정규식 패턴
# ==============================================================================

# 클래스/인터페이스/enum 선언
CLASS_PATTERN = re.compile(
    r'^(?P<indent>\s*)(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)'
    r'(?P<modifiers>(?:public|private|protected|static|abstract|final)\s+)*'
    r'(?P<type>class|interface|enum|record)\s+'
    r'(?P<name>\w+)'
    r'(?:<[^>]+>)?'                        # 제네릭
    r'(?:\s+extends\s+(?P<extends>[\w.<>,\s]+?))?'
    r'(?:\s+implements\s+(?P<implements>[\w.<>,\s]+?))?'
    r'\s*\{',
    re.MULTILINE
)

# 메서드 선언
METHOD_PATTERN = re.compile(
    r'^(?P<indent>\s+)(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)'
    r'(?P<modifiers>(?:public|private|protected|static|abstract|final|synchronized|default|native)\s+)*'
    r'(?P<return_type>[\w<>\[\].,?\s]+?)\s+'
    r'(?P<name>\w+)\s*\('
    r'(?P<params>[^)]*)'
    r'\)',
    re.MULTILINE
)

# Javadoc 추출
JAVADOC_PATTERN = re.compile(r'/\*\*(.*?)\*/', re.DOTALL)


def parse_java_file(file_path: str, source: str) -> dict:
    """Java 파일을 파싱하여 노드와 엣지를 추출합니다."""
    lines = source.splitlines()
    nodes = []
    edges = []
    
    # 패키지 추출
    package = ""
    pkg_match = re.search(r'package\s+([\w.]+)\s*;', source)
    if pkg_match:
        package = pkg_match.group(1)
    
    # 임포트 추출 (엣지 생성용)
    imports = []
    for m in re.finditer(r'import\s+(?:static\s+)?([\w.]+)\s*;', source):
        imports.append(m.group(1))
    
    # 클래스/인터페이스 추출
    current_class = None
    for m in CLASS_PATTERN.finditer(source):
        cls_name = m.group("name")
        cls_type = m.group("type")
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        
        extends = m.group("extends")
        implements = m.group("implements")
        sig_parts = []
        if m.group("modifiers"):
            sig_parts.append(m.group("modifiers").strip())
        sig_parts.append(f"{cls_type} {cls_name}")
        if extends:
            sig_parts.append(f"extends {extends.strip()}")
        if implements:
            sig_parts.append(f"implements {implements.strip()}")
        sig = " ".join(sig_parts)
        
        fqn = f"{file_path}::{cls_name}" if not package else f"{file_path}::{package}.{cls_name}"
        
        # Javadoc 추출
        docstring = _find_javadoc(source, m.start())
        
        # 스켈레톤 생성
        body_lines = lines[start_line - 1:end_line]
        skeleton_std = _generate_class_skeleton(body_lines, cls_name, sig)
        skeleton_min = f"{sig} {{ ...  // {end_line - start_line + 1} lines }}"
        
        cls_node = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": cls_type,
            "name": cls_name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": None,
            "docstring": _truncate(docstring, 200),
            "is_exported": "public" in (m.group("modifiers") or ""),
            "is_async": 0,
            "is_test": cls_name.endswith("Test") or cls_name.endswith("Tests"),
            "raw_body": "\n".join(body_lines),
            "skeleton_standard": skeleton_std,
            "skeleton_minimal": skeleton_min,
            "language": "java"
        }
        nodes.append(cls_node)
        current_class = cls_node
    
    # 메서드 추출
    for m in METHOD_PATTERN.finditer(source):
        method_name = m.group("name")
        # 생성자 또는 제어문 제외
        if method_name in ("if", "for", "while", "switch", "catch", "return", "new"):
            continue
        
        return_type = m.group("return_type").strip()
        params = m.group("params").strip()
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        
        modifiers = (m.group("modifiers") or "").strip()
        sig = f"{modifiers} {return_type} {method_name}({params})".strip()
        
        # 소속 클래스 결정
        parent_name = ""
        if current_class and start_line >= current_class["start_line"] and start_line <= current_class["end_line"]:
            parent_name = current_class["name"]
        
        fqn = f"{file_path}::{parent_name}::{method_name}" if parent_name else f"{file_path}::{method_name}"
        docstring = _find_javadoc(source, m.start())
        
        body_lines = lines[start_line - 1:end_line]
        line_count = end_line - start_line + 1
        skeleton_std = f"{sig} {{\n"
        if docstring:
            skeleton_std += f'    /** {_truncate(docstring, 80)} */\n'
        skeleton_std += f"    ...  // [{line_count} lines]\n}}"
        skeleton_min = f"{return_type} {method_name}(...) // {line_count} lines"
        
        method_node = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "method",
            "name": method_name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": return_type,
            "docstring": _truncate(docstring, 200),
            "is_exported": "public" in modifiers,
            "is_async": 0,
            "is_test": method_name.startswith("test") or "@Test" in (m.group("annotations") or ""),
            "raw_body": "\n".join(body_lines),
            "skeleton_standard": skeleton_std,
            "skeleton_minimal": skeleton_min,
            "language": "java"
        }
        nodes.append(method_node)
        
        # 클래스 → 메서드 엣지
        if current_class and parent_name:
            edges.append({
                "source_id": current_class["id"],
                "target_id": method_node["id"],
                "type": "CONTAINS",
                "call_site_line": start_line
            })
    
    return {"nodes": nodes, "edges": edges}


# ==============================================================================
# 유틸리티
# ==============================================================================

def _find_block_end(lines: list, start_idx: int) -> int:
    """중괄호 매칭으로 블록의 끝 줄 번호를 찾습니다."""
    depth = 0
    found_open = False
    for i in range(start_idx, len(lines)):
        line = lines[i]
        # 문자열/주석 내의 중괄호 무시 (간이 처리)
        stripped = re.sub(r'"[^"]*"', '', line)
        stripped = re.sub(r'//.*$', '', stripped)
        for ch in stripped:
            if ch == '{':
                depth += 1
                found_open = True
            elif ch == '}':
                depth -= 1
                if found_open and depth == 0:
                    return i + 1  # 1-indexed
    return len(lines)

def _find_javadoc(source: str, pos: int) -> str:
    """특정 위치 바로 위의 Javadoc 주석을 찾습니다."""
    before = source[:pos].rstrip()
    match = re.search(r'/\*\*(.*?)\*/\s*$', before, re.DOTALL)
    if match:
        raw = match.group(1)
        # * 제거 및 정리
        cleaned = re.sub(r'^\s*\*\s?', '', raw, flags=re.MULTILINE).strip()
        # @param 등 제거
        cleaned = re.split(r'\n\s*@', cleaned)[0].strip()
        return cleaned
    return ""

def _generate_class_skeleton(body_lines: list, cls_name: str, sig: str) -> str:
    """클래스의 standard 스켈레톤 생성"""
    skeleton = sig + " {\n"
    in_method = False
    brace_depth = 0
    for line in body_lines[1:]:  # 첫 줄(클래스 선언) 스킵
        stripped = line.strip()
        if not in_method and METHOD_PATTERN.match(line):
            skeleton += line.rstrip() + "\n"
            if '{' in line:
                in_method = True
                brace_depth = line.count('{') - line.count('}')
                if brace_depth == 0:
                    in_method = False
                else:
                    skeleton += "        ...\n"
        elif in_method:
            brace_depth += stripped.count('{') - stripped.count('}')
            if brace_depth <= 0:
                skeleton += "    }\n"
                in_method = False
        elif stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
            # 필드 선언 등
            if ";" in stripped and not stripped.startswith("@"):
                skeleton += line.rstrip() + "\n"
    skeleton += "}\n"
    return skeleton

def _truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    first_line = text.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line
