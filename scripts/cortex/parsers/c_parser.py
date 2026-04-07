"""
Pure-Context C/C++ 파서
정규식 기반으로 C/C++ 소스를 노드로 변환
- 함수 정의, 구조체/클래스, enum, 매크로(#define) 추출
"""
import re
import uuid


# ==============================================================================
# 정규식 패턴
# ==============================================================================

# C/C++ 함수 정의 (반환형 함수명(인자) {)
FUNCTION_PATTERN = re.compile(
    r'^(?P<template>template\s*<[^>]+>\s*)?'
    r'(?P<qualifiers>(?:(?:static|inline|virtual|explicit|constexpr|extern)\s+)*)'
    r'(?P<return_type>[\w:*&<>\s,]+?)\s+'
    r'(?P<name>(?:[\w:]+::)?[\w~]+)\s*'
    r'\((?P<params>[^)]*)\)\s*'
    r'(?P<const>const\s*)?'
    r'(?P<override>override\s*)?'
    r'(?P<noexcept>noexcept\s*(?:\([^)]*\))?\s*)?'
    r'\{',
    re.MULTILINE
)

# C++ 클래스/구조체
CLASS_PATTERN = re.compile(
    r'^(?P<template>template\s*<[^>]+>\s*)?'
    r'(?P<type>class|struct)\s+'
    r'(?P<name>\w+)\s*'
    r'(?::\s*(?P<bases>[^{]+?))?\s*\{',
    re.MULTILINE
)

# enum (C/C++)
ENUM_PATTERN = re.compile(
    r'^(?P<kind>enum\s+(?:class\s+)?)'
    r'(?P<name>\w+)\s*'
    r'(?::\s*\w+\s*)?'
    r'\{',
    re.MULTILINE
)

# 매크로 (#define)
MACRO_PATTERN = re.compile(
    r'^#define\s+(?P<name>\w+)(?P<params>\([^)]*\))?\s+(?P<body>.+?)$',
    re.MULTILINE
)

# typedef
TYPEDEF_PATTERN = re.compile(
    r'^typedef\s+(?P<definition>.+?)\s+(?P<name>\w+)\s*;',
    re.MULTILINE
)


def parse_c_file(file_path: str, source: str) -> dict:
    """C/C++ 파일을 파싱하여 노드와 엣지를 추출합니다."""
    lines = source.splitlines()
    nodes = []
    edges = []
    lang = "cpp" if file_path.endswith((".cpp", ".hpp", ".cc", ".cxx")) else "c"

    # 전처리: 문자열/주석 제거한 클린 소스 (패턴 매칭 정확도 향상)
    clean_source = _strip_comments(source)

    # 클래스/구조체 추출
    for m in CLASS_PATTERN.finditer(clean_source):
        name = m.group("name")
        start_line = clean_source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        kind = m.group("type")  # class | struct
        bases = (m.group("bases") or "").strip()
        template = (m.group("template") or "").strip()

        sig_parts = []
        if template:
            sig_parts.append(template)
        sig_parts.append(f"{kind} {name}")
        if bases:
            sig_parts.append(f": {bases}")
        sig = " ".join(sig_parts)

        fqn = f"{file_path}::{name}"
        body = "\n".join(lines[start_line - 1:end_line])
        docstring = _find_comment_above(source, m.start())

        cls_node = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": kind,
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": None,
            "docstring": _truncate(docstring, 200),
            "is_exported": 1,
            "is_async": 0,
            "is_test": name.endswith("Test") or name.startswith("Test"),
            "raw_body": body,
            "skeleton_standard": f"{sig} {{\n    ...\n}};",
            "skeleton_minimal": f"{sig} {{ ... }};",
            "language": lang
        }
        nodes.append(cls_node)

    # 함수 추출 (전처리기 지시자, typedef, 선언문 제외)
    for m in FUNCTION_PATTERN.finditer(clean_source):
        name = m.group("name")
        # 키워드 필터 (if, for, while 등 제어문 제외)
        if name in ("if", "for", "while", "switch", "return", "catch", "sizeof", "typeof", "main"):
            if name != "main":
                continue
        # 전처리기 라인 제외
        line_start = clean_source.rfind("\n", 0, m.start())
        if line_start >= 0 and clean_source[line_start+1:m.start()].lstrip().startswith("#"):
            continue

        start_line = clean_source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        ret_type = m.group("return_type").strip()
        params = m.group("params").strip()
        qualifiers = m.group("qualifiers").strip()
        template = (m.group("template") or "").strip()
        is_const = bool(m.group("const"))

        sig_parts = []
        if template:
            sig_parts.append(template)
        if qualifiers:
            sig_parts.append(qualifiers)
        sig_parts.append(f"{ret_type} {name}({params})")
        if is_const:
            sig_parts.append("const")
        sig = " ".join(sig_parts)

        fqn = f"{file_path}::{name}"
        body = "\n".join(lines[start_line - 1:end_line])
        docstring = _find_comment_above(source, m.start())

        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "function",
            "name": name.split("::")[-1],  # 네임스페이스 제거
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": ret_type,
            "docstring": _truncate(docstring, 200),
            "is_exported": not name.startswith("_"),
            "is_async": 0,
            "is_test": "test" in name.lower(),
            "raw_body": body,
            "skeleton_standard": f"{sig} {{\n    ...\n}}",
            "skeleton_minimal": f"{name.split('::')[-1]}(...)",
            "language": lang
        })

    # enum 추출
    for m in ENUM_PATTERN.finditer(clean_source):
        name = m.group("name")
        start_line = clean_source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        kind = m.group("kind").strip()
        fqn = f"{file_path}::{name}"
        body = "\n".join(lines[start_line - 1:end_line])

        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "enum",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": f"{kind} {name}",
            "return_type": None,
            "docstring": _truncate(_find_comment_above(source, m.start()), 200),
            "is_exported": 1,
            "is_async": 0,
            "is_test": 0,
            "raw_body": body,
            "skeleton_standard": f"{kind} {name} {{ ... }};",
            "skeleton_minimal": f"{kind} {name} {{ ... }};",
            "language": lang
        })

    # 매크로 추출 (함수형 매크로만, 단순 상수는 노이즈)
    for m in MACRO_PATTERN.finditer(source):
        name = m.group("name")
        params = m.group("params")
        if not params:
            continue  # 함수형 매크로만 추출
        start_line = source[:m.start()].count("\n") + 1
        fqn = f"{file_path}::#{name}"

        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "macro",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": start_line,
            "signature": f"#define {name}{params}",
            "return_type": None,
            "docstring": "",
            "is_exported": 1,
            "is_async": 0,
            "is_test": 0,
            "raw_body": m.group(0),
            "skeleton_standard": f"#define {name}{params} ...",
            "skeleton_minimal": f"#define {name}{params}",
            "language": lang
        })

    return {"nodes": nodes, "edges": edges}


# ==============================================================================
# 유틸리티
# ==============================================================================

def _strip_comments(source: str) -> str:
    """C/C++ 주석 제거 (문자열 리터럴 보존)"""
    # 블록 주석 제거
    result = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    # 라인 주석 제거
    result = re.sub(r'//.*$', '', result, flags=re.MULTILINE)
    return result


def _find_block_end(lines: list, start_idx: int) -> int:
    """중괄호 기반 블록 종료 위치 탐색"""
    depth = 0
    found = False
    for i in range(start_idx, len(lines)):
        stripped = re.sub(r'"[^"]*"', '', lines[i])
        stripped = re.sub(r"'[^']*'", '', stripped)
        stripped = re.sub(r'//.*$', '', stripped)
        for ch in stripped:
            if ch == '{':
                depth += 1
                found = True
            elif ch == '}':
                depth -= 1
                if found and depth == 0:
                    return i + 1
    return len(lines)


def _find_comment_above(source: str, pos: int) -> str:
    """매칭 위치 직전의 주석(Doxygen/블록) 추출"""
    before = source[:pos].rstrip()
    # Doxygen 스타일 /** ... */
    match = re.search(r'/\*\*(.*?)\*/\s*$', before, re.DOTALL)
    if match:
        raw = match.group(1)
        cleaned = re.sub(r'^\s*\*\s?', '', raw, flags=re.MULTILINE).strip()
        cleaned = re.split(r'\n\s*@', cleaned)[0].strip()
        return cleaned
    # 연속 // 주석
    lines_before = before.split("\n")
    comment_lines = []
    for line in reversed(lines_before):
        stripped = line.strip()
        if stripped.startswith("//"):
            comment_lines.insert(0, stripped[2:].strip())
        else:
            break
    return "\n".join(comment_lines) if comment_lines else ""


def _truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    first_line = text.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line
