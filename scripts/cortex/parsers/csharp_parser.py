"""
Cortex C# Parser (Unity-Aware)
정규식 기반으로 C# 소스를 노드로 변환

추출 대상:
  - 클래스/인터페이스/구조체/enum (MonoBehaviour 상속 여부 포함)
  - 메서드 (async, IEnumerator 코루틴, 이벤트 콜백 인식)
  - Unity Attribute ([SerializeField], [Header], [MenuItem] 등)
  - 프로퍼티 (get/set accessor)
"""
import re
import uuid

# ==============================================================================
# 지원 확장자 메타데이터 (ParserRegistry 자동 등록)
# ==============================================================================
SUPPORTED_EXTENSIONS = {
    ".cs": ("csharp", lambda file_path, source: parse_csharp_file(file_path, source)),
}

# ==============================================================================
# Unity 특화 판별을 위한 상수
# ==============================================================================
UNITY_BASE_CLASSES = {
    "MonoBehaviour", "ScriptableObject", "Editor", "EditorWindow",
    "NetworkBehaviour", "StateMachineBehaviour", "PlayableBehaviour",
}

UNITY_LIFECYCLE_METHODS = {
    "Awake", "Start", "Update", "FixedUpdate", "LateUpdate",
    "OnEnable", "OnDisable", "OnDestroy", "OnApplicationQuit",
    "OnCollisionEnter", "OnCollisionStay", "OnCollisionExit",
    "OnTriggerEnter", "OnTriggerStay", "OnTriggerExit",
    "OnCollisionEnter2D", "OnCollisionStay2D", "OnCollisionExit2D",
    "OnTriggerEnter2D", "OnTriggerStay2D", "OnTriggerExit2D",
    "OnMouseDown", "OnMouseUp", "OnMouseOver", "OnMouseEnter", "OnMouseExit",
    "OnBecameVisible", "OnBecameInvisible",
    "OnGUI", "OnDrawGizmos", "OnDrawGizmosSelected",
    "OnValidate", "Reset",
}

# ==============================================================================
# 정규식 패턴
# ==============================================================================

# 네임스페이스
NAMESPACE_PATTERN = re.compile(
    r'^namespace\s+(?P<name>[\w.]+)\s*\{',
    re.MULTILINE
)

# 클래스 / 인터페이스 / 구조체 / enum (Attribute 포함)
TYPE_PATTERN = re.compile(
    r'^(?P<attrs>(?:\s*\[[\w.,\s"=()]+\]\s*)*)'          # [Attribute] (0개 이상)
    r'(?P<modifiers>(?:(?:public|private|protected|internal|static|abstract|sealed|partial|readonly|unsafe)\s+)*)'
    r'(?P<kind>class|interface|struct|enum)\s+'
    r'(?P<name>\w+)'
    r'(?:\s*<(?P<generics>[^>]+)>)?'                      # 제네릭 <T>
    r'(?:\s*:\s*(?P<bases>[^{/\n]+?))?'                   # 상속 / 구현
    r'\s*\{',
    re.MULTILINE
)

# 메서드 (생성자, 소멸자 포함 / async / IEnumerator 인식)
METHOD_PATTERN = re.compile(
    r'^(?P<attrs>(?:\s*\[[\w.,\s"=()]+\]\s*)*)'
    r'(?P<modifiers>(?:(?:public|private|protected|internal|static|abstract|virtual|override|sealed|async|extern|new|unsafe)\s+)*)'
    r'(?P<return_type>[\w.<>\[\],?]+(?:\s*\[\])*)\s+'
    r'(?P<name>\w+)\s*'
    r'(?:<(?P<generics>[^>]+)>)?\s*'
    r'\((?P<params>[^)]*)\)\s*'
    r'(?:where\s+[^{]+)?\s*'
    r'(?:\{|=>)',                                          # 블록 or expression body
    re.MULTILINE
)

# 생성자 (클래스명과 동일한 이름 + 파라미터)
CONSTRUCTOR_PATTERN = re.compile(
    r'^(?P<modifiers>(?:(?:public|private|protected|internal)\s+)+)'
    r'(?P<name>\w+)\s*'
    r'\((?P<params>[^)]*)\)\s*'
    r'(?::\s*(?:base|this)\s*\([^)]*\)\s*)?'
    r'\{',
    re.MULTILINE
)

# 프로퍼티
PROPERTY_PATTERN = re.compile(
    r'^(?P<attrs>(?:\s*\[[\w.,\s"=()]+\]\s*)*)'
    r'(?P<modifiers>(?:(?:public|private|protected|internal|static|abstract|virtual|override|new)\s+)*)'
    r'(?P<type>[\w.<>\[\],?]+(?:\s*\[\])*)\s+'
    r'(?P<name>\w+)\s*'
    r'\{[^}]*(?:get|set)[^}]*\}',                         # { get; set; } or { get { } }
    re.MULTILINE
)

# ==============================================================================
# 메인 파서
# ==============================================================================

def parse_csharp_file(file_path: str, source: str) -> dict:
    """C# 파일을 파싱하여 노드와 엣지를 추출합니다."""
    lines = source.splitlines()
    nodes = []
    edges = []

    clean_source = _strip_comments(source)

    # ----- 1. 네임스페이스 수집 (FQN 구성용) -----
    namespaces = []
    for m in NAMESPACE_PATTERN.finditer(clean_source):
        namespaces.append(m.group("name"))
    ns_prefix = namespaces[0] if namespaces else ""

    # ----- 2. 타입 (class / interface / struct / enum) -----
    type_nodes = {}  # name -> node_id (엣지 연결용)
    for m in TYPE_PATTERN.finditer(clean_source):
        name = m.group("name")
        kind = m.group("kind")
        modifiers = (m.group("modifiers") or "").strip()
        bases_raw = (m.group("bases") or "").strip()
        generics = m.group("generics")
        attrs_raw = (m.group("attrs") or "").strip()

        start_line = clean_source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)

        fqn = f"{ns_prefix}.{name}" if ns_prefix else name
        if generics:
            sig = f"{modifiers} {kind} {name}<{generics}>"
        else:
            sig = f"{modifiers} {kind} {name}"
        if bases_raw:
            sig += f" : {bases_raw}"
        sig = sig.strip()

        # Unity: MonoBehaviour 상속 여부
        base_types = [b.strip() for b in bases_raw.split(",")]
        is_mono = any(b in UNITY_BASE_CLASSES for b in base_types)

        docstring = _find_comment_above(source, m.start())
        body = "\n".join(lines[start_line - 1:end_line])
        node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{fqn}"))
        type_nodes[name] = node_id

        nodes.append({
            "id": node_id,
            "type": kind,
            "name": name,
            "fqn": f"{file_path}::{fqn}",
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": None,
            "docstring": _truncate(docstring, 200),
            "is_exported": "public" in modifiers,
            "is_async": 0,
            "is_test": "Test" in attrs_raw or name.endswith("Test") or name.endswith("Tests"),
            "raw_body": _truncate(body, 500),
            "skeleton_standard": f"{sig} {{\n    ...\n}}",
            "skeleton_minimal": f"{kind} {name} {{ ... }}",
            "language": "csharp",
            # Unity 특화 메타데이터
            "unity_mono": is_mono,
            "unity_bases": bases_raw,
        })

        # 상속 엣지 생성
        for base in base_types:
            base = base.split("<")[0].strip()  # 제네릭 제거
            if base:
                edges.append({
                    "source_id": node_id,
                    "target_id": f"__unresolved__::{base}",
                    "type": "INHERITS" if kind in ("class", "struct") else "IMPLEMENTS"
                })

    # ----- 3. 메서드 -----
    seen_fqns = set()
    for m in METHOD_PATTERN.finditer(clean_source):
        name = m.group("name")
        # 키워드 필터
        if name in ("if", "for", "foreach", "while", "switch", "return",
                    "catch", "using", "lock", "new", "typeof", "sizeof",
                    "nameof", "default", "await"):
            continue
        # 타입 선언 직후이면 생성자 패턴이 중복 매칭될 수 있으므로 타입명은 제외
        if name in type_nodes:
            continue

        modifiers = (m.group("modifiers") or "").strip()
        ret_type = m.group("return_type").strip()
        params = (m.group("params") or "").strip()
        attrs_raw = (m.group("attrs") or "").strip()
        generics = m.group("generics")

        start_line = clean_source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        fqn = f"{file_path}::{ns_prefix}.{name}" if ns_prefix else f"{file_path}::{name}"

        # 중복 방지 (오버로드는 파라미터 포함)
        unique_key = f"{fqn}({params})"
        if unique_key in seen_fqns:
            continue
        seen_fqns.add(unique_key)

        gen_str = f"<{generics}>" if generics else ""
        sig = f"{modifiers} {ret_type} {name}{gen_str}({params})".strip()
        is_async = "async" in modifiers
        is_coroutine = ret_type in ("IEnumerator", "IEnumerator<>") or "IEnumerator" in ret_type
        is_lifecycle = name in UNITY_LIFECYCLE_METHODS
        is_test = "Test" in attrs_raw or "test" in name.lower()

        docstring = _find_comment_above(source, m.start())

        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, unique_key)),
            "type": "method",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "signature": sig,
            "return_type": ret_type,
            "docstring": _truncate(docstring, 200),
            "is_exported": "public" in modifiers,
            "is_async": 1 if (is_async or is_coroutine) else 0,
            "is_test": is_test,
            "raw_body": _truncate("\n".join(lines[start_line - 1:end_line]), 500),
            "skeleton_standard": f"{sig} {{\n    ...\n}}",
            "skeleton_minimal": f"{name}(...)",
            "language": "csharp",
            # Unity 특화 메타데이터
            "unity_lifecycle": is_lifecycle,
            "unity_coroutine": is_coroutine,
        })

    # ----- 4. 프로퍼티 -----
    for m in PROPERTY_PATTERN.finditer(clean_source):
        name = m.group("name")
        if name in ("if", "for", "while"):
            continue
        modifiers = (m.group("modifiers") or "").strip()
        prop_type = m.group("type").strip()
        start_line = clean_source[:m.start()].count("\n") + 1
        fqn = f"{file_path}::{ns_prefix}.{name}" if ns_prefix else f"{file_path}::{name}"
        unique_key = f"{fqn}[prop]"
        if unique_key in seen_fqns:
            continue
        seen_fqns.add(unique_key)

        nodes.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, unique_key)),
            "type": "property",
            "name": name,
            "fqn": fqn,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": start_line,
            "signature": f"{modifiers} {prop_type} {name}".strip(),
            "return_type": prop_type,
            "docstring": _truncate(_find_comment_above(source, m.start()), 200),
            "is_exported": "public" in modifiers,
            "is_async": 0,
            "is_test": 0,
            "raw_body": m.group(0),
            "skeleton_standard": f"{prop_type} {name} {{ get; set; }}",
            "skeleton_minimal": f"{name} {{ get; set; }}",
            "language": "csharp",
            "unity_lifecycle": False,
            "unity_coroutine": False,
        })

    return {"nodes": nodes, "edges": edges}


# ==============================================================================
# 유틸리티
# ==============================================================================

def _strip_comments(source: str) -> str:
    """C# 주석 제거 (문자열 리터럴 내부는 보존)"""
    # 블록 주석 /* ... */
    result = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    # XML 문서 주석 및 라인 주석 //
    result = re.sub(r'//.*?$', '', result, flags=re.MULTILINE)
    return result


def _find_block_end(lines: list, start_idx: int) -> int:
    """중괄호 쌍으로 블록 종료 위치 탐색"""
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
    """매칭 위치 직전의 XML 문서 주석 또는 // 주석 추출"""
    before = source[:pos].rstrip()
    # XML 문서 주석 /// ...
    lines_before = before.split("\n")
    xml_lines = []
    for line in reversed(lines_before):
        stripped = line.strip()
        if stripped.startswith("///"):
            xml_lines.insert(0, re.sub(r'///\s?', '', stripped).strip())
        elif stripped.startswith("["):  # Attribute 라인은 스킵
            continue
        else:
            break
    if xml_lines:
        return "\n".join(xml_lines)
    # 일반 블록 주석 /** ... */
    match = re.search(r'/\*\*(.*?)\*/\s*$', before, re.DOTALL)
    if match:
        raw = match.group(1)
        cleaned = re.sub(r'^\s*\*\s?', '', raw, flags=re.MULTILINE).strip()
        return cleaned
    return ""


def _truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    first_line = text.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line
