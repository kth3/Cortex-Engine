"""
Pure-Context TypeScript/JavaScript 파서
정규식 기반으로 TS/JS 소스를 노드로 변환
"""
import re
import uuid

# ==============================================================================
# 지원 확장자 메타데이터
# ==============================================================================
SUPPORTED_EXTENSIONS = {
    ".ts": ("typescript", lambda file_path, source: parse_typescript_file(file_path, source)),
    ".tsx": ("typescript", lambda file_path, source: parse_typescript_file(file_path, source)),
    ".js": ("javascript", lambda file_path, source: parse_typescript_file(file_path, source)),
    ".jsx": ("javascript", lambda file_path, source: parse_typescript_file(file_path, source))
}

# ==============================================================================
# 정규식 패턴
# ==============================================================================

CLASS_PATTERN = re.compile(
    r'^(?P<export>export\s+)?(?P<default>default\s+)?'
    r'(?P<abstract>abstract\s+)?'
    r'class\s+(?P<name>\w+)'
    r'(?:<[^>]+>)?'
    r'(?:\s+extends\s+(?P<extends>[\w.<>,\s]+?))?'
    r'(?:\s+implements\s+(?P<implements>[\w.<>,\s]+?))?'
    r'\s*\{',
    re.MULTILINE
)

INTERFACE_PATTERN = re.compile(
    r'^(?P<export>export\s+)?'
    r'(?:interface|type)\s+(?P<name>\w+)'
    r'(?:<[^>]+>)?'
    r'(?:\s+=\s+|\s+extends\s+[\w.<>,\s]+?\s*)?\{',
    re.MULTILINE
)

FUNCTION_PATTERN = re.compile(
    r'^(?P<export>export\s+)?(?P<default>default\s+)?'
    r'(?P<async>async\s+)?'
    r'function\s+(?P<name>\w+)\s*'
    r'(?:<[^>]+>)?'
    r'\((?P<params>[^)]*)\)'
    r'(?:\s*:\s*(?P<return_type>[^{]+?))?\s*\{',
    re.MULTILINE
)

ARROW_PATTERN = re.compile(
    r'^(?P<export>export\s+)?(?P<kind>const|let|var)\s+'
    r'(?P<name>\w+)\s*'
    r'(?::\s*[^=]+?)?\s*=\s*'
    r'(?P<async>async\s+)?'
    r'(?:\([^)]*\)|[\w]+)\s*(?::\s*[^=]+?)?\s*=>\s*[{\(]?',
    re.MULTILINE
)

METHOD_PATTERN = re.compile(
    r'^(?P<indent>\s+)(?P<async>async\s+)?'
    r'(?P<static>static\s+)?'
    r'(?P<access>public|private|protected)?\s*'
    r'(?P<name>\w+)\s*\((?P<params>[^)]*)\)'
    r'(?:\s*:\s*(?P<return_type>[^{]+?))?\s*\{',
    re.MULTILINE
)


def parse_typescript_file(file_path: str, source: str) -> dict:
    """TypeScript/JavaScript 파일을 파싱하여 노드와 엣지를 추출합니다."""
    lines = source.splitlines()
    nodes = []
    edges = []
    
    # 클래스 추출
    for m in CLASS_PATTERN.finditer(source):
        cls_name = m.group("name")
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        
        sig_parts = []
        if m.group("export"):
            sig_parts.append("export")
        if m.group("abstract"):
            sig_parts.append("abstract")
        sig_parts.append(f"class {cls_name}")
        if m.group("extends"):
            sig_parts.append(f"extends {m.group('extends').strip()}")
        if m.group("implements"):
            sig_parts.append(f"implements {m.group('implements').strip()}")
        sig = " ".join(sig_parts)
        
        fqn = f"{file_path}::{cls_name}"
        body = "\n".join(lines[start_line - 1:end_line])
        docstring = _find_jsdoc(source, m.start())
        
        cls_node = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "class",
            "name": cls_name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": None,
            "docstring": _truncate(docstring, 200),
            "is_exported": bool(m.group("export")),
            "is_async": 0,
            "is_test": cls_name.endswith("Test") or cls_name.endswith("Spec"),
            "raw_body": body,
            "skeleton_standard": f"{sig} {{\n    ...\n}}",
            "skeleton_minimal": f"{sig} {{ ... }}",
            "language": "typescript"
        }
        nodes.append(cls_node)
    
    # 인터페이스/타입 추출
    for m in INTERFACE_PATTERN.finditer(source):
        name = m.group("name")
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        fqn = f"{file_path}::{name}"
        body = "\n".join(lines[start_line - 1:end_line])
        
        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "interface",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": f"interface {name}",
            "return_type": None,
            "docstring": _truncate(_find_jsdoc(source, m.start()), 200),
            "is_exported": bool(m.group("export")),
            "is_async": 0,
            "is_test": 0,
            "raw_body": body,
            "skeleton_standard": f"interface {name} {{\n    ...\n}}",
            "skeleton_minimal": f"interface {name} {{ ... }}",
            "language": "typescript"
        })
    
    # 함수 추출
    for m in FUNCTION_PATTERN.finditer(source):
        name = m.group("name")
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        is_async = bool(m.group("async"))
        ret = (m.group("return_type") or "").strip()
        params = m.group("params").strip()
        prefix = "async " if is_async else ""
        sig = f"{prefix}function {name}({params})" + (f": {ret}" if ret else "")
        fqn = f"{file_path}::{name}"
        body = "\n".join(lines[start_line - 1:end_line])
        
        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "function",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": ret or None,
            "docstring": _truncate(_find_jsdoc(source, m.start()), 200),
            "is_exported": bool(m.group("export")),
            "is_async": int(is_async),
            "is_test": name.startswith("test") or name.endswith("Test"),
            "raw_body": body,
            "skeleton_standard": f"{sig} {{\n    ...\n}}",
            "skeleton_minimal": f"{name}(...)",
            "language": "typescript"
        })
    
    # 화살표 함수 추출
    for m in ARROW_PATTERN.finditer(source):
        name = m.group("name")
        if name in ("if", "for", "while", "return"):
            continue
        start_line = source[:m.start()].count("\n") + 1
        end_line = min(start_line + 50, len(lines))
        # 간이 블록 추적
        for i in range(start_line - 1, min(start_line + 100, len(lines))):
            if i < len(lines) and lines[i].strip() == "}" or lines[i].strip() == "};":
                end_line = i + 1
                break
        
        is_async = bool(m.group("async"))
        sig = f"{'async ' if is_async else ''}const {name} = (...) => {{}}"
        fqn = f"{file_path}::{name}"
        
        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "type": "function",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": None,
            "docstring": "",
            "is_exported": bool(m.group("export")),
            "is_async": int(is_async),
            "is_test": 0,
            "raw_body": "\n".join(lines[start_line - 1:end_line]),
            "skeleton_standard": sig,
            "skeleton_minimal": f"{name}(...)",
            "language": "typescript"
        })
    
    return {"nodes": nodes, "edges": edges}


def _find_block_end(lines: list, start_idx: int) -> int:
    depth = 0
    found = False
    for i in range(start_idx, len(lines)):
        stripped = re.sub(r'"[^"]*"', '', lines[i])
        stripped = re.sub(r"'[^']*'", '', stripped)
        stripped = re.sub(r'`[^`]*`', '', stripped)
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

def _find_jsdoc(source: str, pos: int) -> str:
    before = source[:pos].rstrip()
    match = re.search(r'/\*\*(.*?)\*/\s*$', before, re.DOTALL)
    if match:
        raw = match.group(1)
        cleaned = re.sub(r'^\s*\*\s?', '', raw, flags=re.MULTILINE).strip()
        cleaned = re.split(r'\n\s*@', cleaned)[0].strip()
        return cleaned
    return ""

def _truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    first_line = text.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line
