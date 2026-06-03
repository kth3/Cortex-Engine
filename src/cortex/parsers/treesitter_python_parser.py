from __future__ import annotations

import tree_sitter_python
from cortex.parsers.treesitter_utils import (
    base_node, edge, extract_type_names, make_id, node_end_line, node_line,
    node_text, parser_for, simple_name, txt, unresolved_fqn, unresolved_name,
)


def parse_python_file(file_path: str, source: str) -> dict:
    parser = parser_for(tree_sitter_python.language)
    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node
    module_id = make_id(file_path)
    nodes = [base_node(
        file_path=file_path,
        node_type="module",
        name=file_path.rsplit("/", 1)[-1].removesuffix(".py"),
        fqn=file_path,
        start_line=1,
        end_line=max(1, len(source.splitlines())),
        language="python",
        docstring=_module_docstring(root, source),
        raw_body="",
        is_exported=True,
    )]
    edges = []
    imports = _imports(root, source, module_id, edges)
    seen = set()
    _walk(root, source, file_path, module_id, imports, nodes, edges, [], None, seen)
    return {"nodes": nodes, "edges": edges}


def _walk(node, source, file_path, module_id, imports, nodes, edges, class_stack, current_func_id, seen):
    t = node.type
    if t in {"import_statement", "import_from_statement", "future_import_statement"}:
        return
    if t == "decorated_definition":
        for child in node.children:
            if child.type in {"class_definition", "function_definition"}:
                _walk(child, source, file_path, module_id, imports, nodes, edges, class_stack, current_func_id, seen)
        return
    if t == "class_definition" and current_func_id is None:
        n = _class_node(node, source, file_path, class_stack)
        if n and n["fqn"] not in seen:
            seen.add(n["fqn"])
            nodes.append(n)
            ctx = {"id": n["id"], "name": n["name"]}
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk(child, source, file_path, module_id, imports, nodes, edges, class_stack + [ctx], None, seen)
        return
    if t == "function_definition" and current_func_id is None:
        n = _function_node(node, source, file_path, class_stack)
        if n and n["fqn"] not in seen:
            seen.add(n["fqn"])
            nodes.append(n)
            if class_stack:
                edges.append(edge(class_stack[-1]["id"], n["id"], "CONTAINS", call_site_line=n["start_line"]))
            _annotation_edges(node, source, n["id"], imports, edges)
            body = node.child_by_field_name("body")
            if body:
                _walk(body, source, file_path, module_id, imports, nodes, edges, class_stack, n["id"], seen)
        return
    if t == "call" and current_func_id:
        name = _call_name(node, source)
        if name and not name.startswith("_"):
            target_fqn = imports.get(name)
            edges.append(edge(
                current_func_id,
                unresolved_fqn(target_fqn) if target_fqn else unresolved_name(name),
                "CALLS",
                target_name=name,
                target_kind_hint="function|method",
                target_fqn_hint=target_fqn,
                call_site_line=node_line(node),
            ))
    for child in node.children:
        _walk(child, source, file_path, module_id, imports, nodes, edges, class_stack, current_func_id, seen)


def _imports(root, source, module_id, edges):
    imports = {}
    def visit(node):
        if node.type == "import_statement":
            text = node_text(source, node).replace("import", "", 1)
            for part in text.split(","):
                item = part.strip()
                if not item:
                    continue
                bits = item.split(" as ")
                fqn = bits[0].strip()
                local = bits[-1].strip() if len(bits) > 1 else fqn.split(".")[0]
                imports[local] = fqn
                edges.append(edge(module_id, unresolved_name(local), "IMPORTS", target_name=local, target_kind_hint="module", call_site_line=node_line(node)))
        elif node.type == "import_from_statement":
            text = node_text(source, node)
            if " import " in text:
                mod, names = text.split(" import ", 1)
                mod = mod.replace("from", "", 1).strip()
                for part in names.split(","):
                    raw = part.strip()
                    if not raw or raw == "*":
                        continue
                    bits = raw.split(" as ")
                    name = bits[0].strip()
                    local = bits[-1].strip() if len(bits) > 1 else name
                    fqn = f"{mod}.{name}"
                    imports[local] = fqn
                    edges.append(edge(module_id, unresolved_fqn(fqn), "IMPORTS", target_name=local, target_kind_hint="module", target_fqn_hint=fqn, call_site_line=node_line(node)))
        for child in node.children:
            visit(child)
    visit(root)
    return imports


def _class_node(node, source, file_path, class_stack):
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None
    name = txt(name_node)
    fqn = "::".join([file_path] + [c["name"] for c in class_stack] + [name])
    signature = node_text(source, node).splitlines()[0]
    raw = node_text(source, node)
    return base_node(
        file_path=file_path, node_type="class", name=name, fqn=fqn,
        start_line=node_line(node), end_line=node_end_line(node), language="python",
        signature=signature, docstring=_docstring(node, source), raw_body=raw,
        is_exported=not name.startswith("_"), is_test=name.startswith("Test") or name.endswith("Test"),
        skeleton_standard=f"{signature}\n    ...", skeleton_minimal=f"{signature} ...",
    )


def _function_node(node, source, file_path, class_stack):
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None
    name = txt(name_node)
    fqn = "::".join([file_path] + [c["name"] for c in class_stack] + [name])
    raw = node_text(source, node)
    header = raw.splitlines()[0]
    return_type = None
    if "->" in header:
        return_type = header.split("->", 1)[1].rsplit(":", 1)[0].strip()
    is_async = raw.lstrip().startswith("async def")
    return base_node(
        file_path=file_path, node_type="method" if class_stack else "function", name=name, fqn=fqn,
        start_line=node_line(node), end_line=node_end_line(node), language="python",
        signature=header, return_type=return_type, docstring=_docstring(node, source), raw_body=raw,
        is_exported=not name.startswith("_"), is_async=is_async, is_test=name.startswith("test"),
        skeleton_standard=f"{header}\n    ...", skeleton_minimal=f"{name}(...)"
    )


def _annotation_edges(node, source, source_id, imports, edges):
    header = node_text(source, node).splitlines()[0]
    for name in extract_type_names(header):
        fqn = imports.get(name)
        edges.append(edge(source_id, unresolved_fqn(fqn) if fqn else unresolved_name(name), "ANNOTATED_WITH", target_name=name, target_kind_hint="type", target_fqn_hint=fqn, call_site_line=node_line(node)))


def _call_name(node, source):
    func = node.child_by_field_name("function") or (node.children[0] if node.children else None)
    if not func:
        return None
    text = node_text(source, func)
    return text.split(".")[-1]


def _docstring(node, source):
    body = node.child_by_field_name("body")
    if not body:
        return ""
    for child in body.children:
        if child.type == "expression_statement" and child.children and child.children[0].type == "string":
            return node_text(source, child.children[0]).strip('"\'')
    return ""


def _module_docstring(root, source):
    for child in root.children:
        if child.type == "expression_statement" and child.children and child.children[0].type == "string":
            return node_text(source, child.children[0]).strip('"\'')
    return ""
