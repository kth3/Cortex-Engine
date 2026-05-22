//! TypeScript / TSX 파서.
//!
//! Python `scripts/cortex/parsers/treesitter_ts_parser.py` 의 결과 스키마와 최대한 맞춘다.

use std::collections::HashSet;

use tree_sitter::{Node, Parser};

use crate::common::{uuid5_for, EdgeRecord, NodeRecord, ParseResult};

pub fn parse_ts_file(file_path: &str, source: &str, lang_variant: &str) -> ParseResult {
    let language = if lang_variant == "tsx" {
        tree_sitter_typescript::LANGUAGE_TSX.into()
    } else {
        tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()
    };

    let mut parser = Parser::new();
    if parser.set_language(&language).is_err() {
        return ParseResult::default();
    }

    let Some(tree) = parser.parse(source, None) else {
        return ParseResult::default();
    };

    let root = tree.root_node();
    let bytes = source.as_bytes();
    let module_id = uuid5_for(file_path);
    let module_name = basename_without_ts_ext(file_path);
    let line_count = source.bytes().filter(|b| *b == b'\n').count() as u32 + 1;

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
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body: String::new(),
        skeleton_standard: None,
        skeleton_minimal: None,
        language: lang_variant.to_string(),
    }];
    let mut edges = Vec::new();
    let mut seen_fqns = HashSet::new();

    walk(
        root,
        bytes,
        file_path,
        lang_variant,
        &module_id,
        &mut nodes,
        &mut edges,
        &mut seen_fqns,
    );

    ParseResult { nodes, edges }
}

fn walk(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
    module_id: &str,
    nodes: &mut Vec<NodeRecord>,
    edges: &mut Vec<EdgeRecord>,
    seen_fqns: &mut HashSet<String>,
) {
    match node.kind() {
        "import_statement" => {
            if let Some(source_node) = node.child_by_field_name("source") {
                let import_text = text_of(source_node, src)
                    .trim()
                    .trim_matches(|c| c == '\'' || c == '"' || c == '`');
                if !import_text.is_empty() {
                    let last_segment = import_text
                        .rsplit(|c| c == '/' || c == '\\')
                        .find(|segment| !segment.is_empty())
                        .unwrap_or(import_text);
                    edges.push(EdgeRecord {
                        source_id: module_id.to_string(),
                        target_id: unresolved_name(last_segment),
                        edge_type: "IMPORTS".to_string(),
                        target_name: Some(last_segment.to_string()),
                        target_kind_hint: Some("module".to_string()),
                        target_fqn_hint: None,
                        call_site_line: Some(node.start_position().row as u32 + 1),
                        confidence: 1.0,
                    });
                }
            }
            return;
        }
        "class_declaration" => {
            if let Some(class_node) = build_class_node(node, src, file_path, lang_variant) {
                seen_fqns.insert(class_node.fqn.clone());
                nodes.push(class_node);
            }

            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                walk(
                    child,
                    src,
                    file_path,
                    lang_variant,
                    module_id,
                    nodes,
                    edges,
                    seen_fqns,
                );
            }
            return;
        }
        "interface_declaration" => {
            if let Some(interface_node) = build_interface_node(node, src, file_path, lang_variant) {
                if seen_fqns.insert(interface_node.fqn.clone()) {
                    nodes.push(interface_node);
                }
            }
            return;
        }
        "function_declaration" => {
            if let Some(function_node) = build_function_node(node, src, file_path, lang_variant) {
                if seen_fqns.insert(function_node.fqn.clone()) {
                    nodes.push(function_node);
                }
            }
            return;
        }
        "lexical_declaration" => {
            if let Some(arrow_node) = build_arrow_function_node(node, src, file_path, lang_variant)
            {
                if seen_fqns.insert(arrow_node.fqn.clone()) {
                    nodes.push(arrow_node);
                }
            }
            return;
        }
        "method_definition" => {
            if let Some(method_node) = build_method_node(node, src, file_path, lang_variant) {
                if seen_fqns.insert(method_node.fqn.clone()) {
                    nodes.push(method_node);
                }
            }
            return;
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(
            child,
            src,
            file_path,
            lang_variant,
            module_id,
            nodes,
            edges,
            seen_fqns,
        );
    }
}

fn build_class_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let body = text_of(node, src).to_string();
    let signature = prefix_before_body(&body, &name, "class");
    let _line_count = end_line.saturating_sub(start_line) + 1;
    let raw_body = truncate_chars(&body, 2000);

    Some(NodeRecord {
        id: uuid5_for(&format!("{}::{}", file_path, name)),
        node_type: "class".to_string(),
        name: name.clone(),
        fqn: format!("{}::{}", file_path, name),
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(truncate_chars(&signature, 300)),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body,
        skeleton_standard: Some(format!("class {} {{\n    ...\n}}", name)),
        skeleton_minimal: Some(format!("class {} {{ ... }}", name)),
        language: lang_variant.to_string(),
    })
}

fn build_interface_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let body = text_of(node, src).to_string();

    Some(NodeRecord {
        id: uuid5_for(&format!("{}::{}", file_path, name)),
        node_type: "interface".to_string(),
        name: name.clone(),
        fqn: format!("{}::{}", file_path, name),
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(format!("interface {}", name)),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body: truncate_chars(&body, 2000),
        skeleton_standard: Some(format!("interface {} {{\n    ...\n}}", name)),
        skeleton_minimal: Some(format!("interface {} {{ ... }}", name)),
        language: lang_variant.to_string(),
    })
}

fn build_function_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let body = text_of(node, src).to_string();
    let sig = prefix_before_body(&body, &name, "function");
    let is_exported = body.get(..body.len().min(20)).unwrap_or("").contains("export");
    let is_async = sig.contains("async");

    Some(NodeRecord {
        id: uuid5_for(&format!("{}::{}", file_path, name)),
        node_type: "function".to_string(),
        name: name.clone(),
        fqn: format!("{}::{}", file_path, name),
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(truncate_chars(&sig, 300)),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(if is_exported { 1 } else { 0 }),
        is_async: Some(if is_async { 1 } else { 0 }),
        is_test: Some(if name.to_lowercase().contains("test") { 1 } else { 0 }),
        raw_body: truncate_chars(&body, 2000),
        skeleton_standard: Some(format!(
            "{} {{\n    ...\n}}",
            truncate_chars(&sig, 200)
        )),
        skeleton_minimal: Some(format!("{}(...)", name)),
        language: lang_variant.to_string(),
    })
}

fn build_arrow_function_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
) -> Option<NodeRecord> {
    for child in node.children(&mut node.walk()) {
        if child.kind() != "variable_declarator" {
            continue;
        }

        let value = child.child_by_field_name("value")?;
        if value.kind() != "arrow_function" {
            continue;
        }

        let name = name_of(child, src)?;
        let start_line = node.start_position().row as u32 + 1;
        let end_line = node.end_position().row as u32 + 1;
        let body = text_of(node, src).to_string();
        let is_exported = node
            .parent()
            .map(|parent| parent.kind() == "export_statement")
            .unwrap_or(false)
            || body.get(..body.len().min(20)).unwrap_or("").contains("export");
        let header = body.split_once("=>").map(|(before, _)| before).unwrap_or(&body);
        let is_async = header.contains("async");

        return Some(NodeRecord {
            id: uuid5_for(&format!("{}::{}", file_path, name)),
            node_type: "function".to_string(),
            name: name.clone(),
            fqn: format!("{}::{}", file_path, name),
            file_path: file_path.to_string(),
            start_line,
            end_line,
            signature: Some(format!("const {} = (...) => {{}}", name)),
            return_type: None,
            docstring: Some(String::new()),
            is_exported: Some(if is_exported { 1 } else { 0 }),
            is_async: Some(if is_async { 1 } else { 0 }),
            is_test: Some(0),
            raw_body: truncate_chars(&body, 2000),
            skeleton_standard: Some(format!("const {} = (...) => {{}}", name)),
            skeleton_minimal: Some(format!("{}(...)", name)),
            language: lang_variant.to_string(),
        });
    }

    None
}

fn build_method_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang_variant: &str,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let body = text_of(node, src).to_string();
    let sig = prefix_before_body(&body, &name, "");
    let fqn = build_method_fqn(node, src, file_path);

    Some(NodeRecord {
        id: uuid5_for(&fqn),
        node_type: "method".to_string(),
        name: name.clone(),
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(truncate_chars(&sig, 300)),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body: truncate_chars(&body, 2000),
        skeleton_standard: Some(format!(
            "{} {{\n    ...\n}}",
            truncate_chars(&sig, 200)
        )),
        skeleton_minimal: Some(format!("{}(...)", name)),
        language: lang_variant.to_string(),
    })
}

fn build_method_fqn(node: Node, src: &[u8], file_path: &str) -> String {
    let mut parts = Vec::new();
    let mut current = Some(node);

    while let Some(cur) = current {
        match cur.kind() {
            "class_declaration"
            | "interface_declaration"
            | "struct_declaration"
            | "enum_declaration"
            | "namespace_declaration"
            | "function_declaration"
            | "method_definition"
            | "module_declaration" => {
                if let Some(name) = name_of(cur, src) {
                    parts.insert(0, name);
                }
            }
            _ => {}
        }

        current = cur.parent();
    }

    if parts.is_empty() {
        file_path.to_string()
    } else {
        format!("{}::{}", file_path, parts.join("::"))
    }
}

fn prefix_before_body(body: &str, name: &str, kind: &str) -> String {
    if let Some(idx) = body.find('{') {
        body[..idx].trim().to_string()
    } else if kind == "function" {
        format!("function {}(...)", name)
    } else if kind == "class" {
        format!("class {}", name)
    } else {
        name.to_string()
    }
}

fn basename_without_ts_ext(file_path: &str) -> String {
    let base = file_path
        .rsplit(|c| c == '/' || c == '\\')
        .next()
        .unwrap_or(file_path);
    base.trim_end_matches(".tsx")
        .trim_end_matches(".ts")
        .to_string()
}

fn name_of(node: Node, src: &[u8]) -> Option<String> {
    node.child_by_field_name("name")
        .map(|n| text_of(n, src).to_string())
}

fn text_of<'a>(node: Node, src: &'a [u8]) -> &'a str {
    let start = node.start_byte();
    let end = node.end_byte();
    std::str::from_utf8(&src[start..end]).unwrap_or("")
}

fn truncate_chars(text: &str, max_len: usize) -> String {
    let mut chars = text.chars();
    let mut out = String::new();
    for _ in 0..max_len {
        if let Some(ch) = chars.next() {
            out.push(ch);
        } else {
            break;
        }
    }
    out
}

fn unresolved_name(name: &str) -> String {
    format!("__unresolved__::{}", name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_node_is_first_and_has_expected_identity() {
        let src = r#"
import foo from "react-dom/client";

export const run = async () => 42;
"#;

        let result = parse_ts_file("src/app.ts", src, "typescript");
        assert!(!result.nodes.is_empty());

        let module = &result.nodes[0];
        assert_eq!(module.node_type, "module");
        assert_eq!(module.id, uuid5_for("src/app.ts"));
        assert_eq!(module.name, "app");
        assert_eq!(module.fqn, "src/app.ts");
        assert_eq!(module.start_line, 1);
        assert_eq!(module.language, "typescript");
    }

    #[test]
    fn import_edge_source_id_matches_module_id() {
        let src = r#"
import foo from "react-dom/client";
"#;

        let result = parse_ts_file("src/app.ts", src, "typescript");
        assert_eq!(result.edges.len(), 1);

        let edge = &result.edges[0];
        assert_eq!(edge.source_id, uuid5_for("src/app.ts"));
        assert_eq!(edge.edge_type, "IMPORTS");
        assert_eq!(edge.target_name.as_deref(), Some("client"));
        assert_eq!(edge.target_kind_hint.as_deref(), Some("module"));
        assert_eq!(edge.call_site_line, Some(2));
    }

    #[test]
    fn arrow_function_parses_from_lexical_declaration() {
        let src = r#"
export const run = async () => 42;
"#;

        let result = parse_ts_file("src/app.tsx", src, "tsx");
        let function_node = result
            .nodes
            .iter()
            .find(|node| node.node_type == "function")
            .expect("function node");

        assert_eq!(function_node.name, "run");
        assert_eq!(function_node.fqn, "src/app.tsx::run");
        assert_eq!(function_node.signature.as_deref(), Some("const run = (...) => {}"));
        assert_eq!(function_node.is_exported, Some(1));
        assert_eq!(function_node.is_async, Some(1));
        assert_eq!(function_node.language, "tsx");
    }
}
