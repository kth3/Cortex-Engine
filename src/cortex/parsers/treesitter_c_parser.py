from __future__ import annotations

import tree_sitter_c
import tree_sitter_cpp
from cortex.parsers.treesitter_utils import base_node, make_id, node_end_line, node_line, node_text, parser_for, txt

CPP_EXTS = (".cpp", ".hpp", ".cc", ".cxx")


def parse_c_file(file_path: str, source: str) -> dict:
    is_cpp = file_path.lower().endswith(CPP_EXTS)
    parser = parser_for(tree_sitter_cpp.language if is_cpp else tree_sitter_c.language)
    tree = parser.parse(source.encode("utf-8"))
    nodes = []
    _walk(tree.root_node, source, file_path, "cpp" if is_cpp else "c", nodes)
    return {"nodes": nodes, "edges": []}


def _walk(node, source, file_path, language, nodes):
    if node.type in {"class_specifier", "struct_specifier"}:
        n = _class_like(node, source, file_path, language, "class" if node.type == "class_specifier" else "struct")
        if n:
            nodes.append(n)
    elif node.type == "enum_specifier":
        n = _enum(node, source, file_path, language)
        if n:
            nodes.append(n)
    elif node.type == "function_definition":
        n = _function(node, source, file_path, language)
        if n:
            nodes.append(n)
        return
    elif node.type == "preproc_function_def":
        n = _macro(node, source, file_path, language)
        if n:
            nodes.append(n)
        return
    for child in node.children:
        _walk(child, source, file_path, language, nodes)


def _field(node, name):
    return node.child_by_field_name(name)


def _name(node):
    n = _field(node, "name") or _field(node, "declarator")
    if not n:
        return ""
    text = txt(n)
    return text.split("(", 1)[0].split("::")[-1].strip("*& ")


def _class_like(node, source, file_path, language, kind):
    name = _name(node)
    if not name or not node.child_by_field_name("body"):
        return None
    header = node_text(source, node).split("{", 1)[0].strip()
    fqn = f"{file_path}::{name}"
    return base_node(
        file_path=file_path, node_type=kind, name=name, fqn=fqn,
        start_line=node_line(node), end_line=node_end_line(node), language=language,
        signature=header, raw_body=node_text(source, node), is_exported=True,
        is_test=name.startswith("Test") or name.endswith("Test"),
        skeleton_standard=f"{header} {{\n    ...\n}};", skeleton_minimal=f"{header} {{ ... }};",
    )


def _enum(node, source, file_path, language):
    name = _name(node)
    if not name:
        return None
    raw = node_text(source, node)
    header = raw.split("{", 1)[0].strip()
    return base_node(
        file_path=file_path, node_type="enum", name=name, fqn=f"{file_path}::{name}",
        start_line=node_line(node), end_line=node_end_line(node), language=language,
        signature=header, raw_body=raw, skeleton_standard=f"{header} {{ ... }}", skeleton_minimal=f"{header} {{ ... }}",
    )


def _function(node, source, file_path, language):
    decl = node.child_by_field_name("declarator")
    name = _name(decl or node)
    if not name:
        return None
    raw = node_text(source, node)
    header = raw.split("{", 1)[0].strip()
    return_type = header.split(name, 1)[0].strip() or None
    return base_node(
        file_path=file_path, node_type="function", name=name, fqn=f"{file_path}::{name}",
        start_line=node_line(node), end_line=node_end_line(node), language=language,
        signature=header, return_type=return_type, raw_body=raw,
        is_exported=not name.startswith("_"), is_test=name.startswith("test"),
        skeleton_standard=f"{header} {{\n    ...\n}}", skeleton_minimal=f"{name}(...)"
    )


def _macro(node, source, file_path, language):
    raw = node_text(source, node)
    name = raw.split("(", 1)[0].replace("#define", "", 1).strip()
    if not name:
        return None
    return base_node(
        file_path=file_path, node_type="macro", name=name, fqn=f"{file_path}::{name}",
        start_line=node_line(node), end_line=node_end_line(node), language=language,
        signature=raw.splitlines()[0], raw_body=raw, skeleton_standard=raw.splitlines()[0], skeleton_minimal=name,
    )
