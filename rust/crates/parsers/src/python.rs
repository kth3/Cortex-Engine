//! Python 파서 — `scripts/cortex/parsers/python_parser.py` 대응.
//!
//! tree-sitter-python 기반으로 module/class/function/method/import/call/
//! annotation edge를 추출한다.

use std::collections::{HashMap, HashSet};

use tree_sitter::{Node, Parser};

use crate::common::{
    truncate, unresolved_fqn, unresolved_name, uuid5_for, EdgeRecord, NodeRecord, ParseResult,
};

const PYTHON_LANGUAGE_NAME: &str = "python";

#[derive(Debug, Clone)]
struct ClassCtx {
    id: String,
    name: String,
}

pub fn parse_python_file(file_path: &str, source: &str) -> ParseResult {
    let mut parser = Parser::new();
    if parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .is_err()
    {
        return ParseResult::default();
    }

    let Some(tree) = parser.parse(source, None) else {
        return ParseResult::default();
    };

    let root = tree.root_node();
    let bytes = source.as_bytes();
    let imports_map = build_imports_map(root, bytes);

    let module_id = uuid5_for(file_path);
    let module_name = basename_without_py_ext(file_path);
    let line_count = source.lines().count().max(1) as u32;

    let mut nodes = vec![NodeRecord {
        id: module_id.clone(),
        node_type: "module".to_string(),
        name: module_name,
        fqn: file_path.to_string(),
        file_path: file_path.to_string(),
        start_line: 1,
        end_line: line_count,
        signature: None,
        return_type: None,
        docstring: Some(extract_module_docstring(root, bytes)),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body: String::new(),
        skeleton_standard: None,
        skeleton_minimal: None,
        language: PYTHON_LANGUAGE_NAME.to_string(),
    }];
    let mut edges = Vec::new();
    let mut seen_fqns = HashSet::new();
    let mut class_stack = Vec::new();

    walk(
        root,
        bytes,
        file_path,
        &module_id,
        &imports_map,
        &mut nodes,
        &mut edges,
        &mut seen_fqns,
        &mut class_stack,
        None,
    );

    ParseResult { nodes, edges }
}

fn walk(
    node: Node,
    src: &[u8],
    file_path: &str,
    module_id: &str,
    imports_map: &HashMap<String, String>,
    nodes: &mut Vec<NodeRecord>,
    edges: &mut Vec<EdgeRecord>,
    seen_fqns: &mut HashSet<String>,
    class_stack: &mut Vec<ClassCtx>,
    current_function_id: Option<String>,
) {
    match node.kind() {
        "import_statement" => {
            emit_import_statement(node, src, module_id, edges);
            return;
        }
        "import_from_statement" => {
            emit_import_from_statement(node, src, module_id, edges);
            return;
        }
        "future_import_statement" => {
            return;
        }
        "class_definition" => {
            if current_function_id.is_some() {
                return;
            }

            if let Some(class_node) = build_class_node(node, src, file_path, class_stack) {
                if seen_fqns.insert(class_node.fqn.clone()) {
                    let class_id = class_node.id.clone();
                    let class_name = class_node.name.clone();
                    nodes.push(class_node);
                    class_stack.push(ClassCtx {
                        id: class_id,
                        name: class_name,
                    });

                    if let Some(body) = node.child_by_field_name("body") {
                        walk(
                            body,
                            src,
                            file_path,
                            module_id,
                            imports_map,
                            nodes,
                            edges,
                            seen_fqns,
                            class_stack,
                            None,
                        );
                    }

                    class_stack.pop();
                }
            }
            return;
        }
        "function_definition" => {
            if current_function_id.is_some() {
                return;
            }

            if let Some((function_node, parent_class_id)) =
                build_function_node(node, src, file_path, class_stack, imports_map, edges)
            {
                if seen_fqns.insert(function_node.fqn.clone()) {
                    let function_id = function_node.id.clone();
                    if let Some(parent_class_id) = parent_class_id {
                        edges.push(EdgeRecord {
                            source_id: parent_class_id,
                            target_id: function_id.clone(),
                            edge_type: "CONTAINS".to_string(),
                            target_name: None,
                            target_kind_hint: None,
                            target_fqn_hint: None,
                            call_site_line: Some(function_node.start_line),
                            confidence: 1.0,
                        });
                    }

                    nodes.push(function_node);

                    if let Some(body) = node.child_by_field_name("body") {
                        walk(
                            body,
                            src,
                            file_path,
                            module_id,
                            imports_map,
                            nodes,
                            edges,
                            seen_fqns,
                            class_stack,
                            Some(function_id.clone()),
                        );
                    }
                }
            }
            return;
        }
        "call" => {
            if let Some(function_id) = current_function_id.as_ref() {
                if let Some(target_name) = call_target_name(node, src) {
                    if !target_name.starts_with('_') {
                        let target_id = imports_map
                            .get(&target_name)
                            .map(|fqn| unresolved_fqn(fqn))
                            .unwrap_or_else(|| unresolved_name(&target_name));
                        edges.push(EdgeRecord {
                            source_id: function_id.clone(),
                            target_id,
                            edge_type: "CALLS".to_string(),
                            target_name: Some(target_name),
                            target_kind_hint: Some("function|method".to_string()),
                            target_fqn_hint: None,
                            call_site_line: Some(node.start_position().row as u32 + 1),
                            confidence: 1.0,
                        });
                    }
                }
            }
        }
        "decorated_definition" => {
            if current_function_id.is_some() {
                return;
            }
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(
            child,
            src,
            file_path,
            module_id,
            imports_map,
            nodes,
            edges,
            seen_fqns,
            class_stack,
            current_function_id.clone(),
        );
    }
}

fn build_class_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    class_stack: &[ClassCtx],
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let raw_body = text_of(node, src).to_string();
    let signature = class_signature(node, src, &name);
    let docstring = extract_docstring_from_body(node.child_by_field_name("body"), src);
    let fqn = build_fqn(file_path, class_stack, &name);
    let is_test = name.starts_with("Test") || name.ends_with("Test");
    let is_exported = !name.starts_with('_');
    let short_name = name.clone();

    Some(NodeRecord {
        id: uuid5_for(&fqn),
        node_type: "class".to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature.clone()),
        return_type: None,
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(if is_exported { 1 } else { 0 }),
        is_async: Some(0),
        is_test: Some(if is_test { 1 } else { 0 }),
        raw_body: truncate_chars(&raw_body, 2000),
        skeleton_standard: Some(format!("{}\n    ...", signature)),
        skeleton_minimal: Some(format!("class {}(...)", short_name)),
        language: PYTHON_LANGUAGE_NAME.to_string(),
    })
}

fn build_function_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    class_stack: &[ClassCtx],
    imports_map: &HashMap<String, String>,
    edges: &mut Vec<EdgeRecord>,
) -> Option<(NodeRecord, Option<String>)> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let raw_body = text_of(node, src).to_string();
    let signature = function_signature(node, src, &name);
    let docstring = extract_docstring_from_body(node.child_by_field_name("body"), src);
    let is_async = text_of(node, src).trim_start().starts_with("async def");
    let fqn = build_fqn(file_path, class_stack, &name);
    let is_test = name.starts_with("test_") || name.starts_with("test");
    let is_exported = !name.starts_with('_');
    let return_type = node
        .child_by_field_name("return_type")
        .map(|n| text_of(n, src).trim().to_string())
        .filter(|s| !s.is_empty());
    let parent_class_id = class_stack.last().map(|ctx| ctx.id.clone());
    let short_name = name.clone();
    let source_id = uuid5_for(&fqn);

    emit_annotation_edges(
        edges,
        &source_id,
        node.child_by_field_name("parameters"),
        src,
        start_line,
        imports_map,
    );
    emit_annotation_edges(
        edges,
        &source_id,
        node.child_by_field_name("return_type"),
        src,
        start_line,
        imports_map,
    );

    Some((
        NodeRecord {
            id: source_id,
            node_type: if parent_class_id.is_some() {
                "method".to_string()
            } else {
                "function".to_string()
            },
            name,
            fqn,
            file_path: file_path.to_string(),
            start_line,
            end_line,
            signature: Some(signature.clone()),
            return_type,
            docstring: Some(truncate(&docstring, 200)),
            is_exported: Some(if is_exported { 1 } else { 0 }),
            is_async: Some(if is_async { 1 } else { 0 }),
            is_test: Some(if is_test { 1 } else { 0 }),
            raw_body: truncate_chars(&raw_body, 2000),
            skeleton_standard: Some(format!("{}\n    ...", signature)),
            skeleton_minimal: Some(format!("{}(...)", short_name)),
            language: PYTHON_LANGUAGE_NAME.to_string(),
        },
        parent_class_id,
    ))
}

fn emit_import_statement(node: Node, src: &[u8], module_id: &str, edges: &mut Vec<EdgeRecord>) {
    let mut seen = HashSet::new();
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        if child.kind() == "aliased_import" {
            let alias = child
                .child_by_field_name("alias")
                .map(|n| text_of(n, src).to_string())
                .unwrap_or_default();
            let name = child
                .child_by_field_name("name")
                .map(|n| dotted_name_head(text_of(n, src)))
                .unwrap_or_default();
            let target_name = if alias.is_empty() { name } else { alias };
            if target_name.is_empty() || !seen.insert(target_name.clone()) {
                continue;
            }
            edges.push(import_edge(
                module_id,
                &target_name,
                None,
                node.start_position().row as u32 + 1,
            ));
        } else if child.kind() == "dotted_name" {
            let target_name = dotted_name_head(text_of(child, src));
            if target_name.is_empty() || !seen.insert(target_name.clone()) {
                continue;
            }
            edges.push(import_edge(
                module_id,
                &target_name,
                None,
                node.start_position().row as u32 + 1,
            ));
        }
    }
}

fn emit_import_from_statement(
    node: Node,
    src: &[u8],
    module_id: &str,
    edges: &mut Vec<EdgeRecord>,
) {
    let Some(module_name_node) = node.child_by_field_name("module_name") else {
        return;
    };
    if module_name_node.kind() != "dotted_name" {
        return;
    }

    let module_name = text_of(module_name_node, src).trim().to_string();
    if module_name.is_empty() {
        return;
    }

    let mut seen = HashSet::new();
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        match child.kind() {
            "aliased_import" => {
                let alias = child
                    .child_by_field_name("alias")
                    .map(|n| text_of(n, src).to_string())
                    .unwrap_or_default();
                let name = child
                    .child_by_field_name("name")
                    .map(|n| text_of(n, src).to_string())
                    .unwrap_or_default();
                let target_fqn = format!("{}.{}", module_name, name);
                let target_name = if alias.is_empty() {
                    name.clone()
                } else {
                    alias
                };
                if target_name.is_empty() || !seen.insert(target_name.clone()) {
                    continue;
                }
                edges.push(import_edge(
                    module_id,
                    &target_name,
                    Some(&target_fqn),
                    node.start_position().row as u32 + 1,
                ));
            }
            "dotted_name" => {
                let name = text_of(child, src).trim().to_string();
                if name.is_empty() || !seen.insert(name.clone()) {
                    continue;
                }
                edges.push(import_edge(
                    module_id,
                    &name,
                    Some(&format!("{}.{}", module_name, name)),
                    node.start_position().row as u32 + 1,
                ));
            }
            _ => {}
        }
    }
}

fn import_edge(
    module_id: &str,
    target_name: &str,
    target_fqn: Option<&str>,
    call_site_line: u32,
) -> EdgeRecord {
    let target_id = target_fqn
        .map(unresolved_fqn)
        .unwrap_or_else(|| unresolved_name(target_name));

    EdgeRecord {
        source_id: module_id.to_string(),
        target_id,
        edge_type: "IMPORTS".to_string(),
        target_name: Some(target_name.to_string()),
        target_kind_hint: Some("module".to_string()),
        target_fqn_hint: target_fqn.map(|s| s.to_string()),
        call_site_line: Some(call_site_line),
        confidence: 1.0,
    }
}

fn emit_annotation_edges(
    edges: &mut Vec<EdgeRecord>,
    source_id: &str,
    node: Option<Node>,
    src: &[u8],
    call_site_line: u32,
    imports_map: &HashMap<String, String>,
) {
    let Some(node) = node else {
        return;
    };

    let mut seen = HashSet::new();
    let mut candidates = Vec::new();

    match node.kind() {
        "parameters" => {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                match child.kind() {
                    "typed_parameter" | "typed_default_parameter" => {
                        if let Some(type_node) = child.child_by_field_name("type") {
                            candidates.push(text_of(type_node, src).to_string());
                        }
                    }
                    _ => {}
                }
            }
        }
        "type" => {
            candidates.push(text_of(node, src).to_string());
        }
        _ => {}
    }

    for candidate in candidates {
        for name in extract_type_names(&candidate) {
            if name.starts_with('_') || !seen.insert(name.clone()) {
                continue;
            }

            let target_id = imports_map
                .get(&name)
                .map(|fqn| unresolved_fqn(fqn))
                .unwrap_or_else(|| unresolved_name(&name));

            edges.push(EdgeRecord {
                source_id: source_id.to_string(),
                target_id,
                edge_type: "ANNOTATED_WITH".to_string(),
                target_name: Some(name),
                target_kind_hint: Some("type".to_string()),
                target_fqn_hint: None,
                call_site_line: Some(call_site_line),
                confidence: 1.0,
            });
        }
    }
}

fn build_imports_map(root: Node, src: &[u8]) -> HashMap<String, String> {
    let mut imports_map = HashMap::new();
    collect_imports(root, src, &mut imports_map);
    imports_map
}

fn collect_imports(node: Node, src: &[u8], imports_map: &mut HashMap<String, String>) {
    match node.kind() {
        "import_statement" => {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if child.kind() == "aliased_import" {
                    let alias = child
                        .child_by_field_name("alias")
                        .map(|n| text_of(n, src).to_string())
                        .unwrap_or_default();
                    let name = child
                        .child_by_field_name("name")
                        .map(|n| text_of(n, src).to_string())
                        .unwrap_or_default();
                    let local_name = if alias.is_empty() {
                        dotted_name_head(&name)
                    } else {
                        alias
                    };
                    if !local_name.is_empty() && !name.is_empty() {
                        imports_map.insert(local_name, name);
                    }
                } else if child.kind() == "dotted_name" {
                    let name = text_of(child, src).trim().to_string();
                    let local_name = dotted_name_tail(&name);
                    if !local_name.is_empty() && !name.is_empty() {
                        imports_map.insert(local_name, name);
                    }
                }
            }
        }
        "import_from_statement" => {
            if let Some(module_name) = node.child_by_field_name("module_name") {
                if module_name.kind() == "dotted_name" {
                    let module_name = text_of(module_name, src).trim().to_string();
                    if !module_name.is_empty() {
                        let mut cursor = node.walk();
                        for child in node.named_children(&mut cursor) {
                            if child.kind() == "aliased_import" {
                                let alias = child
                                    .child_by_field_name("alias")
                                    .map(|n| text_of(n, src).to_string())
                                    .unwrap_or_default();
                                let name = child
                                    .child_by_field_name("name")
                                    .map(|n| text_of(n, src).to_string())
                                    .unwrap_or_default();
                                let local_name = if alias.is_empty() {
                                    name.clone()
                                } else {
                                    alias
                                };
                                if !local_name.is_empty() && !name.is_empty() {
                                    imports_map
                                        .insert(local_name, format!("{}.{}", module_name, name));
                                }
                            } else if child.kind() == "dotted_name" {
                                let name = text_of(child, src).trim().to_string();
                                if !name.is_empty() {
                                    imports_map
                                        .insert(name.clone(), format!("{}.{}", module_name, name));
                                }
                            }
                        }
                    }
                }
            }
        }
        "future_import_statement" => {}
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        collect_imports(child, src, imports_map);
    }
}

fn build_fqn(file_path: &str, class_stack: &[ClassCtx], name: &str) -> String {
    let mut parts: Vec<&str> = class_stack.iter().map(|ctx| ctx.name.as_str()).collect();
    parts.push(name);
    format!("{}::{}", file_path, parts.join("::"))
}

fn class_signature(node: Node, src: &[u8], name: &str) -> String {
    let base = node
        .child_by_field_name("superclasses")
        .map(|n| text_of(n, src).trim().to_string())
        .filter(|s| !s.is_empty());
    match base {
        Some(base) => format!("class {}{}:", name, base),
        None => format!("class {}:", name),
    }
}

fn function_signature(node: Node, src: &[u8], name: &str) -> String {
    let parameters = node
        .child_by_field_name("parameters")
        .map(|n| strip_outer_parens(text_of(n, src)))
        .unwrap_or_default();
    let return_type = node
        .child_by_field_name("return_type")
        .map(|n| text_of(n, src).trim().to_string())
        .filter(|s| !s.is_empty());
    let prefix = if text_of(node, src).trim_start().starts_with("async def") {
        "async "
    } else {
        ""
    };
    let suffix = return_type
        .map(|ty| format!(" -> {}", ty))
        .unwrap_or_default();
    format!("{}def {}({}){}:", prefix, name, parameters, suffix)
}

fn extract_module_docstring(root: Node, src: &[u8]) -> String {
    let mut cursor = root.walk();
    for child in root.named_children(&mut cursor) {
        if let Some(doc) = docstring_from_statement(child, src) {
            return doc;
        }
        break;
    }
    String::new()
}

fn extract_docstring_from_body(body: Option<Node>, src: &[u8]) -> String {
    let Some(body) = body else {
        return String::new();
    };

    let mut cursor = body.walk();
    for child in body.named_children(&mut cursor) {
        if let Some(doc) = docstring_from_statement(child, src) {
            return doc;
        }
        break;
    }
    String::new()
}

fn docstring_from_statement(node: Node, src: &[u8]) -> Option<String> {
    match node.kind() {
        "expression_statement" => {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                match child.kind() {
                    "string" | "concatenated_string" => {
                        return Some(strip_python_string_literal(text_of(child, src)));
                    }
                    _ => {}
                }
            }
            None
        }
        "string" | "concatenated_string" => Some(strip_python_string_literal(text_of(node, src))),
        _ => None,
    }
}

fn strip_python_string_literal(raw: &str) -> String {
    let text = raw.trim();
    if text.len() >= 6 && text.starts_with("\"\"\"") && text.ends_with("\"\"\"") {
        return text[3..text.len() - 3].trim().to_string();
    }
    if text.len() >= 6 && text.starts_with("'''") && text.ends_with("'''") {
        return text[3..text.len() - 3].trim().to_string();
    }
    if text.len() >= 2 && text.starts_with('"') && text.ends_with('"') {
        return text[1..text.len() - 1].trim().to_string();
    }
    if text.len() >= 2 && text.starts_with('\'') && text.ends_with('\'') {
        return text[1..text.len() - 1].trim().to_string();
    }
    text.to_string()
}

fn call_target_name(node: Node, src: &[u8]) -> Option<String> {
    let function = node.child_by_field_name("function")?;
    let raw = text_of(function, src).trim();
    if raw.is_empty() {
        return None;
    }

    let candidate = match function.kind() {
        "identifier" => raw.to_string(),
        "attribute" => function
            .child_by_field_name("attribute")
            .map(|n| text_of(n, src).to_string())
            .unwrap_or_else(|| dotted_name_tail(raw)),
        "dotted_name" => dotted_name_tail(raw),
        "call" => call_target_name(function, src)?,
        _ => dotted_name_tail(raw),
    };

    if candidate.is_empty() {
        None
    } else {
        Some(candidate)
    }
}

fn dotted_name_tail(text: &str) -> String {
    text.split(|c| c == '.' || c == ':')
        .filter(|segment| !segment.is_empty())
        .last()
        .unwrap_or("")
        .trim()
        .to_string()
}

fn dotted_name_head(text: &str) -> String {
    text.split(|c| c == '.' || c == ':')
        .filter(|segment| !segment.is_empty())
        .next()
        .unwrap_or("")
        .trim()
        .to_string()
}

fn basename_without_py_ext(file_path: &str) -> String {
    file_path
        .rsplit(|c| c == '/' || c == '\\')
        .next()
        .unwrap_or(file_path)
        .trim_end_matches(".py")
        .to_string()
}

fn name_of(node: Node, src: &[u8]) -> Option<String> {
    node.child_by_field_name("name")
        .map(|n| text_of(n, src).to_string())
        .filter(|s| !s.is_empty())
}

fn text_of<'a>(node: Node, src: &'a [u8]) -> &'a str {
    let start = node.start_byte();
    let end = node.end_byte();
    std::str::from_utf8(&src[start..end]).unwrap_or("")
}

fn strip_outer_parens(text: &str) -> String {
    let trimmed = text.trim();
    if trimmed.starts_with('(') && trimmed.ends_with(')') && trimmed.len() >= 2 {
        trimmed[1..trimmed.len() - 1].trim().to_string()
    } else {
        trimmed.to_string()
    }
}

fn truncate_chars(text: &str, max_len: usize) -> String {
    text.chars().take(max_len).collect()
}

fn extract_type_names(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for token in text.split(|c: char| !c.is_ascii_alphanumeric() && c != '_') {
        if !token.is_empty() {
            out.push(token.to_string());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_node_is_first_and_imports_are_linked_to_module() {
        let src = r#"
"""module doc"""
from pkg.helper import helper as h
import os.path

class Demo:
    pass
"#;

        let result = parse_python_file("src/app.py", src);
        assert!(!result.nodes.is_empty());

        let module = &result.nodes[0];
        assert_eq!(module.node_type, "module");
        assert_eq!(module.id, uuid5_for("src/app.py"));
        assert_eq!(module.name, "app");
        assert_eq!(module.fqn, "src/app.py");
        assert_eq!(module.docstring.as_deref(), Some("module doc"));

        assert!(result
            .edges
            .iter()
            .any(|edge| edge.edge_type == "IMPORTS" && edge.source_id == module.id));
    }

    #[test]
    fn class_method_calls_and_annotations_are_extracted() {
        let src = r#"
from pkg.helper import helper as h

class Demo(Base):
    """class doc"""

    async def run(self, value: Foo, count: int) -> Bar:
        h(value)
        return value
"#;

        let result = parse_python_file("src/app.py", src);
        let class_node = result
            .nodes
            .iter()
            .find(|node| node.node_type == "class")
            .expect("class node");
        let method_node = result
            .nodes
            .iter()
            .find(|node| node.node_type == "method")
            .expect("method node");

        assert_eq!(class_node.fqn, "src/app.py::Demo");
        assert_eq!(method_node.fqn, "src/app.py::Demo::run");
        assert_eq!(method_node.is_async, Some(1));
        assert_eq!(method_node.return_type.as_deref(), Some("Bar"));

        assert!(result
            .edges
            .iter()
            .any(|edge| edge.edge_type == "CONTAINS" && edge.source_id == class_node.id));
        assert!(result
            .edges
            .iter()
            .any(|edge| edge.edge_type == "CALLS" && edge.source_id == method_node.id));
        assert!(result
            .edges
            .iter()
            .any(|edge| edge.edge_type == "ANNOTATED_WITH" && edge.source_id == method_node.id));
    }
}
