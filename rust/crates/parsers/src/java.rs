//! Java 파서 — Python `parsers/java_parser.py` 대응 (tree-sitter-java 기반).
//!
//! Python regex 파서를 tree-sitter로 교체 (정확도 향상이 본 작업의 본래 목적).
//! 추출 대상: class / interface / enum / record + method / constructor.
//! 엣지: 클래스 → 메서드 CONTAINS.

use std::collections::HashMap;
use tree_sitter::{Node, Parser};

use crate::common::{truncate, uuid5_for, EdgeRecord, NodeRecord, ParseResult};

pub fn parse_java_file(file_path: &str, source: &str) -> ParseResult {
    let mut parser = Parser::new();
    if parser
        .set_language(&tree_sitter_java::LANGUAGE.into())
        .is_err()
    {
        return ParseResult::default();
    }

    let Some(tree) = parser.parse(source, None) else {
        return ParseResult::default();
    };
    let root = tree.root_node();
    let bytes = source.as_bytes();

    let package = extract_package(root, bytes);

    let mut nodes: Vec<NodeRecord> = Vec::new();
    let mut edges: Vec<EdgeRecord> = Vec::new();
    // 클래스 노드의 byte_range 시작 → (id, end_byte, start_line, end_line)
    let mut class_ranges: Vec<ClassCtx> = Vec::new();

    walk(root, bytes, file_path, &package, &mut nodes, &mut edges, &mut class_ranges);

    ParseResult { nodes, edges }
}

#[derive(Debug, Clone)]
struct ClassCtx {
    id: String,
    name: String,
    start_byte: usize,
    end_byte: usize,
}

fn walk(
    node: Node,
    src: &[u8],
    file_path: &str,
    package: &str,
    nodes: &mut Vec<NodeRecord>,
    edges: &mut Vec<EdgeRecord>,
    classes: &mut Vec<ClassCtx>,
) {
    let kind = node.kind();

    let class_kind = match kind {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        "record_declaration" => Some("record"),
        _ => None,
    };

    if let Some(ctype) = class_kind {
        if let Some((cls_node, cls_ctx)) = build_class(node, src, file_path, package, ctype) {
            nodes.push(cls_node);
            classes.push(cls_ctx);
        }
    } else if matches!(kind, "method_declaration" | "constructor_declaration") {
        if let Some((method_node, parent_id)) = build_method(node, src, file_path, classes) {
            if let Some(pid) = parent_id {
                edges.push(EdgeRecord {
                    source_id: pid,
                    target_id: method_node.id.clone(),
                    edge_type: "CONTAINS".to_string(),
                    target_name: None,
                    target_kind_hint: None,
                    target_fqn_hint: None,
                    call_site_line: Some(method_node.start_line),
                    confidence: 1.0,
                });
            }
            nodes.push(method_node);
        }
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(child, src, file_path, package, nodes, edges, classes);
    }
}

fn extract_package(root: Node, src: &[u8]) -> String {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_declaration" {
            // package_declaration → scoped_identifier
            let mut inner = child.walk();
            for c in child.children(&mut inner) {
                if c.kind() == "scoped_identifier" || c.kind() == "identifier" {
                    return text_of(c, src).to_string();
                }
            }
        }
    }
    String::new()
}

fn text_of<'a>(node: Node, src: &'a [u8]) -> &'a str {
    let start = node.start_byte();
    let end = node.end_byte();
    std::str::from_utf8(&src[start..end]).unwrap_or("")
}

fn child_by_field<'a>(node: &Node<'a>, field: &str) -> Option<Node<'a>> {
    node.child_by_field_name(field)
}

/// 노드 시작 직전의 block_comment Javadoc(`/** ... */`) 추출.
fn find_javadoc(node: Node, src: &[u8]) -> String {
    let parent = match node.parent() {
        Some(p) => p,
        None => return String::new(),
    };

    let mut cursor = parent.walk();
    let mut prev: Option<Node> = None;
    for child in parent.children(&mut cursor) {
        if child.id() == node.id() {
            break;
        }
        prev = Some(child);
    }

    if let Some(prev_node) = prev {
        if prev_node.kind() == "block_comment" {
            let raw = text_of(prev_node, src);
            if raw.starts_with("/**") {
                // 본문 추출
                let inner = raw.trim_start_matches("/**").trim_end_matches("*/");
                // 줄별 ' * ' prefix 제거 + @param 등 분리
                let mut lines: Vec<&str> = inner.lines().collect();
                if let Some(first) = lines.first() {
                    if first.trim().is_empty() {
                        lines.remove(0);
                    }
                }
                let cleaned: String = lines
                    .iter()
                    .map(|l| l.trim_start().trim_start_matches('*').trim_start().to_string())
                    .collect::<Vec<_>>()
                    .join("\n");
                let before_at: &str = cleaned.split("\n@").next().unwrap_or("");
                return before_at.trim().to_string();
            }
        }
    }
    String::new()
}

fn build_class(
    node: Node,
    src: &[u8],
    file_path: &str,
    package: &str,
    ctype: &str,
) -> Option<(NodeRecord, ClassCtx)> {
    let name_node = child_by_field(&node, "name")?;
    let name = text_of(name_node, src).to_string();
    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;
    let modifiers = collect_modifiers(node, src);
    let extends = field_text(node, "superclass", src);
    let implements = field_text(node, "interfaces", src);
    let docstring = find_javadoc(node, src);

    let mut sig_parts: Vec<String> = Vec::new();
    if !modifiers.is_empty() {
        sig_parts.push(modifiers.join(" "));
    }
    sig_parts.push(format!("{} {}", ctype, name));
    if let Some(ext) = extends.as_ref() {
        sig_parts.push(format!("extends {}", ext.trim_start_matches("extends").trim()));
    }
    if let Some(impls) = implements.as_ref() {
        sig_parts.push(format!("implements {}", impls.trim_start_matches("implements").trim()));
    }
    let signature = sig_parts.join(" ");

    let fqn = if package.is_empty() {
        format!("{}::{}", file_path, name)
    } else {
        format!("{}::{}.{}", file_path, package, name)
    };
    let id = uuid5_for(&fqn);

    let raw_body = text_of(node, src).to_string();
    let skeleton_min = format!("{} {{ ...  // {} lines }}", signature, end_line - start_line + 1);
    // 표준 스켈레톤: 시그니처 + 내부 메서드 시그니처 + ... (간략화 버전)
    let skeleton_std = format!("{} {{\n    ...\n}}", signature);

    let is_test = name.ends_with("Test") || name.ends_with("Tests");
    let is_exported = modifiers.iter().any(|m| m == "public");

    let nr = NodeRecord {
        id: id.clone(),
        node_type: ctype.to_string(),
        name: name.clone(),
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature),
        return_type: None,
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(if is_exported { 1 } else { 0 }),
        is_async: Some(0),
        is_test: Some(if is_test { 1 } else { 0 }),
        raw_body,
        skeleton_standard: Some(skeleton_std),
        skeleton_minimal: Some(skeleton_min),
        language: "java".to_string(),
    };

    let ctx = ClassCtx {
        id,
        name,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
    };
    Some((nr, ctx))
}

fn build_method(
    node: Node,
    src: &[u8],
    file_path: &str,
    classes: &[ClassCtx],
) -> Option<(NodeRecord, Option<String>)> {
    let name_node = child_by_field(&node, "name")?;
    let name = text_of(name_node, src).to_string();
    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;
    let modifiers = collect_modifiers(node, src);
    let return_type = field_text(node, "type", src).unwrap_or_default();
    let params = field_text(node, "parameters", src).unwrap_or_else(|| "()".to_string());
    let docstring = find_javadoc(node, src);

    // 부모 클래스: 가장 가까이 둘러싼 class
    let parent: Option<&ClassCtx> = classes
        .iter()
        .filter(|c| c.start_byte <= node.start_byte() && node.end_byte() <= c.end_byte)
        .max_by_key(|c| c.start_byte);

    let modifiers_str = modifiers.join(" ");
    let signature = if return_type.is_empty() {
        format!("{} {}{}", modifiers_str, name, params).trim().to_string()
    } else {
        format!("{} {} {}{}", modifiers_str, return_type, name, params)
            .trim()
            .to_string()
    };

    let fqn = match parent {
        Some(p) => format!("{}::{}::{}", file_path, p.name, name),
        None => format!("{}::{}", file_path, name),
    };
    let id = uuid5_for(&fqn);

    let raw_body = text_of(node, src).to_string();
    let line_count = end_line.saturating_sub(start_line) + 1;
    let mut skel_std = format!("{} {{\n", signature);
    if !docstring.is_empty() {
        skel_std.push_str(&format!("    /** {} */\n", truncate(&docstring, 80)));
    }
    skel_std.push_str(&format!("    ...  // [{} lines]\n}}", line_count));
    let skel_min = format!("{} {}(...) // {} lines", return_type, name, line_count);

    let is_test = name.starts_with("test") || has_annotation(node, src, "Test");
    let is_exported = modifiers.iter().any(|m| m == "public");

    let nr = NodeRecord {
        id,
        node_type: "method".to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature),
        return_type: Some(return_type),
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(if is_exported { 1 } else { 0 }),
        is_async: Some(0),
        is_test: Some(if is_test { 1 } else { 0 }),
        raw_body,
        skeleton_standard: Some(skel_std),
        skeleton_minimal: Some(skel_min),
        language: "java".to_string(),
    };

    Some((nr, parent.map(|p| p.id.clone())))
}

/// modifiers 자식 노드 내 키워드 토큰 수집 (public/private/static/final 등).
fn collect_modifiers(node: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "modifiers" {
            let mut inner = child.walk();
            for m in child.children(&mut inner) {
                let t = m.kind();
                // marker_annotation / annotation 은 modifiers에서 제외
                if t.starts_with('@') || t.contains("annotation") {
                    continue;
                }
                let s = text_of(m, src).trim().to_string();
                if !s.is_empty() {
                    out.push(s);
                }
            }
        }
    }
    out
}

fn field_text(node: Node, field: &str, src: &[u8]) -> Option<String> {
    node.child_by_field_name(field)
        .map(|n| text_of(n, src).to_string())
}

fn has_annotation(node: Node, src: &[u8], name: &str) -> bool {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "modifiers" {
            let mut inner = child.walk();
            for m in child.children(&mut inner) {
                let k = m.kind();
                if k == "marker_annotation" || k == "annotation" {
                    let text = text_of(m, src);
                    if text.contains(name) {
                        return true;
                    }
                }
            }
        }
    }
    false
}

// HashMap 사용 위치는 없지만 future use를 위해 보존
#[allow(dead_code)]
fn _unused() {
    let _: HashMap<String, String> = HashMap::new();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_simple_class() {
        let src = r#"
package com.example;

public class Hello {
    public String greet(String name) {
        return "Hello, " + name;
    }
}
"#;
        let result = parse_java_file("a/Hello.java", src);
        assert_eq!(result.nodes.len(), 2); // class + method
        let class = result.nodes.iter().find(|n| n.node_type == "class").unwrap();
        assert_eq!(class.name, "Hello");
        assert_eq!(class.fqn, "a/Hello.java::com.example.Hello");
        let method = result.nodes.iter().find(|n| n.node_type == "method").unwrap();
        assert_eq!(method.name, "greet");
        assert_eq!(method.fqn, "a/Hello.java::Hello::greet");
        assert_eq!(method.return_type.as_deref(), Some("String"));
        assert_eq!(result.edges.len(), 1);
        assert_eq!(result.edges[0].edge_type, "CONTAINS");
    }

    #[test]
    fn parse_interface() {
        let src = "interface Foo { void bar(); }";
        let result = parse_java_file("Foo.java", src);
        let iface = result.nodes.iter().find(|n| n.node_type == "interface").unwrap();
        assert_eq!(iface.name, "Foo");
    }

    #[test]
    fn parse_test_class() {
        let src = "public class FooTest { @Test public void testFoo() {} }";
        let result = parse_java_file("a.java", src);
        let cls = result.nodes.iter().find(|n| n.node_type == "class").unwrap();
        assert_eq!(cls.is_test, Some(1));
        let m = result.nodes.iter().find(|n| n.node_type == "method").unwrap();
        assert_eq!(m.is_test, Some(1));
    }
}
