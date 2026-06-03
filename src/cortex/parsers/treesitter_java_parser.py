from __future__ import annotations

import tree_sitter_java
from cortex.parsers.treesitter_utils import base_node, edge, make_id, node_end_line, node_line, node_text, parser_for, txt


def parse_java_file(file_path: str, source: str) -> dict:
    parser = parser_for(tree_sitter_java.language)
    tree = parser.parse(source.encode("utf-8"))
    package = _package(tree.root_node, source)
    nodes, edges = [], []
    classes = []
    _walk(tree.root_node, source, file_path, package, nodes, edges, classes)
    return {"nodes": nodes, "edges": edges}


def _walk(node, source, file_path, package, nodes, edges, classes):
    kind_map = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum", "record_declaration": "record"}
    if node.type in kind_map:
        n = _class_node(node, source, file_path, package, kind_map[node.type])
        if n:
            nodes.append(n)
            classes.append({"id": n["id"], "start": node.start_byte, "end": node.end_byte, "name": n["name"]})
    elif node.type in {"method_declaration", "constructor_declaration"}:
        n = _method_node(node, source, file_path, classes)
        if n:
            nodes.append(n)
            parent = _parent_class(node, classes)
            if parent:
                edges.append(edge(parent["id"], n["id"], "CONTAINS", call_site_line=n["start_line"]))
    for child in node.children:
        _walk(child, source, file_path, package, nodes, edges, classes)


def _package(root, source):
    for child in root.children:
        if child.type == "package_declaration":
            return node_text(source, child).replace("package", "", 1).replace(";", "").strip()
    return ""


def _name(node):
    n = node.child_by_field_name("name")
    return txt(n) if n else ""


def _class_node(node, source, file_path, package, kind):
    name = _name(node)
    if not name:
        return None
    fqn_name = f"{package}.{name}" if package else name
    fqn = f"{file_path}::{fqn_name}"
    header = node_text(source, node).split("{", 1)[0].strip()
    raw = node_text(source, node)
    return base_node(
        file_path=file_path, node_type=kind, name=name, fqn=fqn,
        start_line=node_line(node), end_line=node_end_line(node), language="java",
        signature=header, raw_body=raw, is_exported="public" in header,
        is_test=name.startswith("Test") or name.endswith("Test"),
        skeleton_standard=f"{header} {{\n    ...\n}}", skeleton_minimal=f"{header} {{ ... }}",
    )


def _method_node(node, source, file_path, classes):
    name = _name(node)
    if not name and node.type == "constructor_declaration":
        parent = _parent_class(node, classes)
        name = parent["name"] if parent else "<init>"
    if not name:
        return None
    parent = _parent_class(node, classes)
    fqn = f"{file_path}::{parent['name']}::{name}" if parent else f"{file_path}::{name}"
    raw = node_text(source, node)
    header = raw.split("{", 1)[0].strip().rstrip(";")
    return_type = None
    before_params = header.split("(", 1)[0].split()
    if len(before_params) >= 2 and node.type != "constructor_declaration":
        return_type = before_params[-2]
    return base_node(
        file_path=file_path, node_type="method", name=name, fqn=fqn,
        start_line=node_line(node), end_line=node_end_line(node), language="java",
        signature=header, return_type=return_type, raw_body=raw,
        is_exported="public" in header, is_test=name.startswith("test"),
        skeleton_standard=f"{header} {{\n    ...\n}}", skeleton_minimal=f"{name}(...)"
    )


def _parent_class(node, classes):
    matches = [c for c in classes if c["start"] <= node.start_byte and node.end_byte <= c["end"]]
    return matches[-1] if matches else None
