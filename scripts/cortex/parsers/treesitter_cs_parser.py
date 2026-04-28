"""
Cortex Tree-sitter C# 파서
Tree-sitter 0.25 기반. 직접 트리 순회(walk) 방식.
"""
import re
from tree_sitter import Parser
from cortex.parsers.treesitter_utils import (
    CS_LANGUAGE, txt, name_of, truncate, make_id, build_fqn, extract_type_names,
)

# ── C# 빌트인 필터 (기존 csharp_parser.py 동일) ────────────────
_CS_BUILTIN_TYPES = frozenset({
    "void", "int", "float", "double", "string", "bool", "byte",
    "char", "long", "object", "var", "dynamic", "decimal",
    "short", "uint", "ulong", "ushort", "sbyte",
    "String", "Int32", "Int64", "Boolean", "Object", "Char",
    "Byte", "Double", "Single", "Decimal", "Nullable",
    "List", "Dictionary", "HashSet", "Queue", "Stack", "Array",
    "IEnumerator", "IEnumerable", "IList", "IDictionary", "ICollection",
    "Task", "ValueTask", "Action", "Func", "Predicate", "Tuple",
    "CancellationToken", "Exception",
    "Vector2", "Vector3", "Vector4", "Quaternion", "Color", "Rect",
    "Transform", "GameObject", "Component", "MonoBehaviour",
    "ScriptableObject", "Coroutine",
    "Debug", "Mathf", "Time", "Input", "Physics",
    "WaitForSeconds", "WaitForEndOfFrame", "WaitForFixedUpdate",
    "System", "Collections", "Generic", "T", "TKey", "TValue",
})

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

# ── 파서 레지스트리 ──────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {}
if CS_LANGUAGE:
    SUPPORTED_EXTENSIONS[".cs"] = ("csharp", lambda fp, src: parse_csharp_file(fp, src))


# ══════════════════════════════════════════════════════════════════
# 메인 파서
# ══════════════════════════════════════════════════════════════════

def parse_csharp_file(file_path: str, source: str) -> dict:
    parser = Parser(CS_LANGUAGE)
    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node
    line_count = source.count("\n") + 1

    module_id = make_id(file_path)
    nodes = [{
        "id": module_id, "type": "module",
        "name": file_path.rsplit("/", 1)[-1].replace(".cs", ""),
        "fqn": file_path, "file_path": file_path,
        "start_line": 1, "end_line": line_count,
        "signature": None, "return_type": None,
        "docstring": "", "is_exported": 1, "is_async": 0, "is_test": 0,
        "raw_body": "", "skeleton_standard": None, "skeleton_minimal": None,
        "language": "csharp",
    }]
    edges = []
    type_map = {}
    seen_fqns = set()

    def _walk(node):
        t = node.type

        # ── using 디렉티브 → IMPORTS ──
        if t == "using_directive":
            for child in node.children:
                if child.type in ("identifier", "qualified_name"):
                    ns = txt(child)
                    last = ns.split(".")[-1]
                    edges.append({
                        "source_id": module_id,
                        "target_id": f"__unresolved__::{last}",
                        "type": "IMPORTS",
                        "call_site_line": node.start_point[0] + 1,
                    })
            return

        # ── 타입 선언 (class/interface/struct/enum) ──
        if t in ("class_declaration", "interface_declaration",
                 "struct_declaration", "enum_declaration"):
            kind = t.replace("_declaration", "")
            name = name_of(node)
            if not name:
                for child in node.children:
                    _walk(child)
                return

            fqn = build_fqn(node, file_path)
            node_id = make_id(fqn)
            type_map[name] = node_id
            sl = node.start_point[0] + 1
            el = node.end_point[0] + 1

            base_list = node.child_by_field_name("bases")
            bases_str = txt(base_list) if base_list else ""
            is_mono = any(b in UNITY_BASE_CLASSES for b in bases_str.replace(",", " ").split())

            body_text = txt(node)
            sig_end = body_text.find("{")
            sig = body_text[:sig_end].strip() if sig_end > 0 else f"{kind} {name}"

            nodes.append({
                "id": node_id, "type": kind, "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": sl, "end_line": el,
                "signature": truncate(sig, 300), "return_type": None,
                "docstring": "", "is_exported": 1, "is_async": 0,
                "is_test": "Test" in name,
                "raw_body": truncate(body_text, 2000),
                "skeleton_standard": f"{kind} {name} {{\n    ...\n}}",
                "skeleton_minimal": f"{kind} {name} {{ ... }}",
                "language": "csharp",
                "unity_mono": is_mono, "unity_bases": bases_str,
            })

            if base_list:
                for child in base_list.children:
                    bn = txt(child).split("<")[0].strip().rstrip(",").strip()
                    if bn and bn not in (":", ",", "{", "}"):
                        etype = "INHERITS" if kind in ("class", "struct") else "IMPLEMENTS"
                        edges.append({
                            "source_id": node_id,
                            "target_id": f"__unresolved__::{bn}",
                            "type": etype,
                        })

            for child in node.children:
                _walk(child)
            return

        # ── 메서드 / 생성자 ──
        if t in ("method_declaration", "constructor_declaration"):
            name = name_of(node)
            if not name:
                return
            fqn = build_fqn(node, file_path)
            if fqn in seen_fqns:
                return
            seen_fqns.add(fqn)

            sl = node.start_point[0] + 1
            el = node.end_point[0] + 1
            method_id = make_id(fqn)
            body_text = txt(node)

            sig_end = body_text.find("{")
            sig = body_text[:sig_end].strip() if sig_end > 0 else name

            ret_node = node.child_by_field_name("type")
            ret_type = txt(ret_node) if ret_node else None

            modifiers = " ".join(txt(c) for c in node.children if c.type == "modifier")
            is_async = "async" in modifiers
            is_coroutine = bool(ret_type and "IEnumerator" in ret_type)

            nodes.append({
                "id": method_id, "type": "method", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": sl, "end_line": el,
                "signature": truncate(sig, 300), "return_type": ret_type,
                "docstring": "", "is_exported": "public" in modifiers,
                "is_async": 1 if (is_async or is_coroutine) else 0,
                "is_test": "Test" in name,
                "raw_body": truncate(body_text, 2000),
                "skeleton_standard": f"{truncate(sig, 200)} {{\n    ...\n}}",
                "skeleton_minimal": f"{name}(...)",
                "language": "csharp",
                "unity_lifecycle": name in UNITY_LIFECYCLE_METHODS,
                "unity_coroutine": is_coroutine,
            })

            _extract_body_edges(node, method_id, edges)
            _extract_type_annotations(node, method_id, edges)
            return

        # ── 프로퍼티 ──
        if t == "property_declaration":
            name = name_of(node)
            if not name:
                return
            fqn = build_fqn(node, file_path)
            if fqn in seen_fqns:
                return
            seen_fqns.add(fqn)
            ptype_node = node.child_by_field_name("type")
            ptype = txt(ptype_node) if ptype_node else ""
            nodes.append({
                "id": make_id(fqn), "type": "property", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                "signature": f"{ptype} {name}", "return_type": ptype,
                "docstring": "", "is_exported": 1, "is_async": 0, "is_test": 0,
                "raw_body": txt(node),
                "skeleton_standard": f"{ptype} {name} {{ get; set; }}",
                "skeleton_minimal": f"{name} {{ get; set; }}",
                "language": "csharp",
            })
            return

        # ── 기타 노드 → 재귀 ──
        for child in node.children:
            _walk(child)

    _walk(root)
    return {"nodes": nodes, "edges": edges}


# ══════════════════════════════════════════════════════════════════
# 엣지 추출 헬퍼
# ══════════════════════════════════════════════════════════════════

def _extract_body_edges(method_node, method_id, edges):
    """메서드 본문을 재귀 탐색하여 CALLS 엣지 추출"""
    seen = set()
    body = method_node.child_by_field_name("body")
    if not body:
        return

    def _walk_body(node):
        if node.type == "invocation_expression":
            func = node.child_by_field_name("function")
            if func:
                target = txt(func).split(".")[-1].split("<")[0]
                if target and target not in _CS_BUILTIN_TYPES and target not in seen:
                    seen.add(target)
                    edges.append({
                        "source_id": method_id,
                        "target_id": f"__unresolved__::{target}",
                        "type": "CALLS",
                        "call_site_line": node.start_point[0] + 1,
                    })
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node:
                target = txt(type_node).split("<")[0].split(".")[-1]
                if target and target not in _CS_BUILTIN_TYPES and target not in seen:
                    seen.add(target)
                    edges.append({
                        "source_id": method_id,
                        "target_id": f"__unresolved__::{target}",
                        "type": "CALLS",
                        "call_site_line": node.start_point[0] + 1,
                    })
        for child in node.children:
            _walk_body(child)

    _walk_body(body)


def _extract_type_annotations(method_node, method_id, edges):
    """파라미터/리턴 타입에서 ANNOTATED_WITH 엣지 추출"""
    seen = set()
    ret = method_node.child_by_field_name("type")
    if ret:
        for name in extract_type_names(txt(ret)):
            if name not in _CS_BUILTIN_TYPES and name not in seen:
                seen.add(name)
                edges.append({
                    "source_id": method_id,
                    "target_id": f"__unresolved__::{name}",
                    "type": "ANNOTATED_WITH",
                    "call_site_line": ret.start_point[0] + 1,
                })
    params = method_node.child_by_field_name("parameters")
    if params:
        for name in extract_type_names(txt(params)):
            if name not in _CS_BUILTIN_TYPES and name not in seen:
                seen.add(name)
                edges.append({
                    "source_id": method_id,
                    "target_id": f"__unresolved__::{name}",
                    "type": "ANNOTATED_WITH",
                    "call_site_line": params.start_point[0] + 1,
                })
