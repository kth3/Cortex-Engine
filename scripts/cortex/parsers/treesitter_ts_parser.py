"""
Cortex Tree-sitter TypeScript/TSX 파서
공통 유틸은 treesitter_utils.py에서 import.
"""
from tree_sitter import Parser
from cortex.parsers.treesitter_utils import (
    TS_LANGUAGE, TSX_LANGUAGE, txt, name_of, truncate, make_id, build_fqn,
)

# ── 파서 레지스트리 ──────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {}
if TS_LANGUAGE:
    SUPPORTED_EXTENSIONS[".ts"] = ("typescript", lambda fp, src: parse_ts_file(fp, src, "typescript"))
if TSX_LANGUAGE:
    SUPPORTED_EXTENSIONS[".tsx"] = ("typescript", lambda fp, src: parse_ts_file(fp, src, "tsx"))


def parse_ts_file(file_path: str, source: str, lang_variant: str = "typescript") -> dict:
    """Tree-sitter 기반 TypeScript/TSX 파싱"""
    lang_obj = TSX_LANGUAGE if lang_variant == "tsx" else TS_LANGUAGE
    if not lang_obj:
        return {"nodes": [], "edges": []}

    parser = Parser(lang_obj)
    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node

    module_id = make_id(file_path)
    nodes = []
    edges = []
    seen_fqns = set()

    def _walk(node):
        t = node.type

        # ── import ──
        if t == "import_statement":
            src_node = node.child_by_field_name("source")
            if src_node:
                mod = txt(src_node).strip("'\"")
                last = mod.split("/")[-1]
                edges.append({
                    "source_id": module_id,
                    "target_id": f"__unresolved__::{last}",
                    "type": "IMPORTS",
                    "call_site_line": node.start_point[0] + 1,
                })
            return

        # ── class ──
        if t == "class_declaration":
            name = name_of(node)
            if not name:
                for c in node.children:
                    _walk(c)
                return
            fqn = f"{file_path}::{name}"
            body = txt(node)
            sig_end = body.find("{")
            sig = body[:sig_end].strip() if sig_end > 0 else f"class {name}"
            nodes.append({
                "id": make_id(fqn), "type": "class", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                "signature": truncate(sig, 300), "return_type": None,
                "docstring": "", "is_exported": 1, "is_async": 0, "is_test": 0,
                "raw_body": truncate(body, 2000),
                "skeleton_standard": f"class {name} {{\n    ...\n}}",
                "skeleton_minimal": f"class {name} {{ ... }}",
                "language": "typescript",
            })
            for c in node.children:
                _walk(c)
            return

        # ── interface ──
        if t == "interface_declaration":
            name = name_of(node)
            if not name:
                return
            fqn = f"{file_path}::{name}"
            nodes.append({
                "id": make_id(fqn), "type": "interface", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                "signature": f"interface {name}", "return_type": None,
                "docstring": "", "is_exported": 1, "is_async": 0, "is_test": 0,
                "raw_body": truncate(txt(node), 2000),
                "skeleton_standard": f"interface {name} {{\n    ...\n}}",
                "skeleton_minimal": f"interface {name} {{ ... }}",
                "language": "typescript",
            })
            return

        # ── function declaration ──
        if t == "function_declaration":
            name = name_of(node)
            if not name:
                return
            fqn = f"{file_path}::{name}"
            if fqn in seen_fqns:
                return
            seen_fqns.add(fqn)
            body = txt(node)
            sig_end = body.find("{")
            sig = body[:sig_end].strip() if sig_end > 0 else f"function {name}(...)"
            nodes.append({
                "id": make_id(fqn), "type": "function", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                "signature": truncate(sig, 300), "return_type": None,
                "docstring": "", "is_exported": "export" in sig,
                "is_async": int("async" in sig), "is_test": "test" in name.lower(),
                "raw_body": truncate(body, 2000),
                "skeleton_standard": f"{truncate(sig,200)} {{\n    ...\n}}",
                "skeleton_minimal": f"{name}(...)",
                "language": "typescript",
            })
            return

        # ── arrow function (const xxx = () => {}) ──
        if t == "lexical_declaration":
            for vd in node.children:
                if vd.type == "variable_declarator":
                    val = vd.child_by_field_name("value")
                    if val and val.type == "arrow_function":
                        nm = name_of(vd)
                        if not nm:
                            continue
                        fqn = f"{file_path}::{nm}"
                        if fqn in seen_fqns:
                            continue
                        seen_fqns.add(fqn)
                        body = txt(node)
                        is_async = "async" in body[:body.find("=>")] if "=>" in body else False
                        nodes.append({
                            "id": make_id(fqn), "type": "function", "name": nm,
                            "fqn": fqn, "file_path": file_path,
                            "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                            "signature": f"const {nm} = (...) => {{}}",
                            "return_type": None, "docstring": "",
                            "is_exported": "export" in txt(node)[:20],
                            "is_async": int(is_async), "is_test": 0,
                            "raw_body": truncate(body, 2000),
                            "skeleton_standard": f"const {nm} = (...) => {{}}",
                            "skeleton_minimal": f"{nm}(...)",
                            "language": "typescript",
                        })
                        return
            return

        # ── method definition (class 내부) ──
        if t == "method_definition":
            name = name_of(node)
            if not name:
                return
            parent_fqn = build_fqn(node, file_path)
            fqn = parent_fqn if "::" in parent_fqn else f"{file_path}::{name}"
            if fqn in seen_fqns:
                return
            seen_fqns.add(fqn)
            body = txt(node)
            sig_end = body.find("{")
            sig = body[:sig_end].strip() if sig_end > 0 else name
            nodes.append({
                "id": make_id(fqn), "type": "method", "name": name,
                "fqn": fqn, "file_path": file_path,
                "start_line": node.start_point[0]+1, "end_line": node.end_point[0]+1,
                "signature": truncate(sig, 300), "return_type": None,
                "docstring": "", "is_exported": 1, "is_async": 0, "is_test": 0,
                "raw_body": truncate(body, 2000),
                "skeleton_standard": f"{truncate(sig,200)} {{\n    ...\n}}",
                "skeleton_minimal": f"{name}(...)",
                "language": "typescript",
            })
            return

        # ── 기타 → 재귀 ──
        for child in node.children:
            _walk(child)

    _walk(root)
    return {"nodes": nodes, "edges": edges}
