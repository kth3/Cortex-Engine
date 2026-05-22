//! C# parser for Cortex.
//!
//! This is the tree-sitter-backed counterpart to the Python C# parser used by
//! the existing indexing pipeline.

use std::collections::HashSet;

use tree_sitter::{Node, Parser};

use crate::common::{unresolved_name, uuid5_for, EdgeRecord, NodeRecord, ParseResult};

const CSHARP_LANGUAGE_NAME: &str = "csharp";

const BUILTIN_TYPES: &[&str] = &[
    "void",
    "int",
    "float",
    "double",
    "string",
    "bool",
    "byte",
    "char",
    "long",
    "object",
    "var",
    "dynamic",
    "decimal",
    "short",
    "uint",
    "ulong",
    "ushort",
    "sbyte",
    "String",
    "Int32",
    "Int64",
    "Boolean",
    "Object",
    "Char",
    "Byte",
    "Double",
    "Single",
    "Decimal",
    "Nullable",
    "List",
    "Dictionary",
    "HashSet",
    "Queue",
    "Stack",
    "Array",
    "IEnumerator",
    "IEnumerable",
    "IList",
    "IDictionary",
    "ICollection",
    "Task",
    "ValueTask",
    "Action",
    "Func",
    "Predicate",
    "Tuple",
    "CancellationToken",
    "Exception",
    "Vector2",
    "Vector3",
    "Vector4",
    "Quaternion",
    "Color",
    "Rect",
    "Transform",
    "GameObject",
    "Component",
    "MonoBehaviour",
    "ScriptableObject",
    "Coroutine",
    "Debug",
    "Mathf",
    "Time",
    "Input",
    "Physics",
    "WaitForSeconds",
    "WaitForEndOfFrame",
    "WaitForFixedUpdate",
    "System",
    "Collections",
    "Generic",
    "T",
    "TKey",
    "TValue",
];

#[allow(dead_code)]
const UNITY_BASE_CLASSES: &[&str] = &[
    "MonoBehaviour",
    "ScriptableObject",
    "Editor",
    "EditorWindow",
    "NetworkBehaviour",
    "StateMachineBehaviour",
    "PlayableBehaviour",
];

#[allow(dead_code)]
const UNITY_LIFECYCLE_METHODS: &[&str] = &[
    "Awake",
    "Start",
    "Update",
    "FixedUpdate",
    "LateUpdate",
    "OnEnable",
    "OnDisable",
    "OnDestroy",
    "OnApplicationQuit",
    "OnCollisionEnter",
    "OnCollisionStay",
    "OnCollisionExit",
    "OnTriggerEnter",
    "OnTriggerStay",
    "OnTriggerExit",
    "OnCollisionEnter2D",
    "OnCollisionStay2D",
    "OnCollisionExit2D",
    "OnTriggerEnter2D",
    "OnTriggerStay2D",
    "OnTriggerExit2D",
    "OnMouseDown",
    "OnMouseUp",
    "OnMouseOver",
    "OnMouseEnter",
    "OnMouseExit",
    "OnBecameVisible",
    "OnBecameInvisible",
    "OnGUI",
    "OnDrawGizmos",
    "OnDrawGizmosSelected",
    "OnValidate",
    "Reset",
];

#[derive(Debug, Clone)]
struct TypeCtx {
    name: String,
}

pub fn parse_csharp_file(file_path: &str, source: &str) -> ParseResult {
    let mut parser = Parser::new();
    if parser
        .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
        .is_err()
    {
        return ParseResult::default();
    }

    let Some(tree) = parser.parse(source, None) else {
        return ParseResult::default();
    };

    let root = tree.root_node();
    let bytes = source.as_bytes();
    let module_id = uuid5_for(file_path);
    let module_name = basename_without_cs_ext(file_path);
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
        language: CSHARP_LANGUAGE_NAME.to_string(),
    }];
    let mut edges = Vec::new();
    let mut seen_fqns = HashSet::new();
    let mut type_stack: Vec<TypeCtx> = Vec::new();

    walk(
        root,
        bytes,
        file_path,
        &module_id,
        &mut nodes,
        &mut edges,
        &mut seen_fqns,
        &mut type_stack,
        None,
    );

    ParseResult { nodes, edges }
}

fn walk(
    node: Node,
    src: &[u8],
    file_path: &str,
    module_id: &str,
    nodes: &mut Vec<NodeRecord>,
    edges: &mut Vec<EdgeRecord>,
    seen_fqns: &mut HashSet<String>,
    type_stack: &mut Vec<TypeCtx>,
    current_method_id: Option<String>,
) {
    match node.kind() {
        "using_directive" => {
            if let Some(source_node) = node.child_by_field_name("name") {
                let raw = text_of(source_node, src).trim().to_string();
                let target_name = raw
                    .rsplit(|c| c == '.' || c == ':' || c == '/')
                    .find(|segment| !segment.is_empty())
                    .unwrap_or(raw.as_str())
                    .trim()
                    .to_string();
                if !target_name.is_empty() {
                    edges.push(EdgeRecord {
                        source_id: module_id.to_string(),
                        target_id: unresolved_name(&target_name),
                        edge_type: "IMPORTS".to_string(),
                        target_name: Some(target_name),
                        target_kind_hint: Some("module".to_string()),
                        target_fqn_hint: None,
                        call_site_line: Some(node.start_position().row as u32 + 1),
                        confidence: 1.0,
                    });
                }
            }
            return;
        }
        "class_declaration"
        | "interface_declaration"
        | "struct_declaration"
        | "enum_declaration" => {
            if let Some((node_record, type_name)) =
                build_type_node(node, src, file_path, type_stack, edges)
            {
                if seen_fqns.insert(node_record.fqn.clone()) {
                    type_stack.push(TypeCtx { name: type_name });
                    nodes.push(node_record);

                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        walk(
                            child,
                            src,
                            file_path,
                            module_id,
                            nodes,
                            edges,
                            seen_fqns,
                            type_stack,
                            current_method_id.clone(),
                        );
                    }
                    type_stack.pop();
                    return;
                }
            }
        }
        "method_declaration" | "constructor_declaration" => {
            if let Some(method_node) = build_method_node(node, src, file_path, type_stack, edges) {
                if seen_fqns.insert(method_node.fqn.clone()) {
                    let method_id = method_node.id.clone();
                    nodes.push(method_node);

                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        walk(
                            child,
                            src,
                            file_path,
                            module_id,
                            nodes,
                            edges,
                            seen_fqns,
                            type_stack,
                            Some(method_id.clone()),
                        );
                    }
                    return;
                }
            }
        }
        "property_declaration" => {
            if let Some(property_node) =
                build_property_node(node, src, file_path, type_stack, edges)
            {
                if seen_fqns.insert(property_node.fqn.clone()) {
                    nodes.push(property_node);
                }
            }
            return;
        }
        "invocation_expression" => {
            if let Some(method_id) = current_method_id.as_ref() {
                if let Some(target_name) = invocation_target_name(node, src) {
                    edges.push(EdgeRecord {
                        source_id: method_id.clone(),
                        target_id: unresolved_name(&target_name),
                        edge_type: "CALLS".to_string(),
                        target_name: Some(target_name),
                        target_kind_hint: Some("method|type".to_string()),
                        target_fqn_hint: None,
                        call_site_line: Some(node.start_position().row as u32 + 1),
                        confidence: 1.0,
                    });
                }
            }
        }
        "object_creation_expression" => {
            if let Some(method_id) = current_method_id.as_ref() {
                if let Some(target_name) = object_creation_target_name(node, src) {
                    edges.push(EdgeRecord {
                        source_id: method_id.clone(),
                        target_id: unresolved_name(&target_name),
                        edge_type: "CALLS".to_string(),
                        target_name: Some(target_name),
                        target_kind_hint: Some("type".to_string()),
                        target_fqn_hint: None,
                        call_site_line: Some(node.start_position().row as u32 + 1),
                        confidence: 1.0,
                    });
                }
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
            nodes,
            edges,
            seen_fqns,
            type_stack,
            current_method_id.clone(),
        );
    }
}

fn build_type_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    type_stack: &[TypeCtx],
    edges: &mut Vec<EdgeRecord>,
) -> Option<(NodeRecord, String)> {
    let kind = match node.kind() {
        "class_declaration" => "class",
        "interface_declaration" => "interface",
        "struct_declaration" => "struct",
        "enum_declaration" => "enum",
        _ => return None,
    };

    let name = name_of(node, src)?;
    let fqn = build_fqn(file_path, type_stack, Some(&name));
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let raw_body = text_of(node, src).to_string();
    let signature = text_before_body(&raw_body, kind, &name);

    let display_name = name.clone();

    let node_record = NodeRecord {
        id: uuid5_for(&fqn),
        node_type: kind.to_string(),
        name: name.clone(),
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(truncate_chars(&signature, 300)),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(if name.ends_with("Test") || name.ends_with("Tests") {
            1
        } else {
            0
        }),
        raw_body: truncate_chars(&raw_body, 2000),
        skeleton_standard: Some(format!("{} {} {{\n    ...\n}}", kind, display_name)),
        skeleton_minimal: Some(format!("{} {} {{ ... }}", kind, display_name)),
        language: CSHARP_LANGUAGE_NAME.to_string(),
    };

    emit_base_edges(node, src, &node_record.id, kind, edges);

    Some((node_record, name))
}

fn build_method_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    type_stack: &[TypeCtx],
    edges: &mut Vec<EdgeRecord>,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let fqn = build_fqn(file_path, type_stack, Some(&name));
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let raw_body = text_of(node, src).to_string();
    let signature = text_before_body(&raw_body, "", &name);
    let return_type = node
        .child_by_field_name("type")
        .map(|n| text_of(n, src).trim().to_string())
        .filter(|s| !s.is_empty());
    let is_async = has_async_modifier(node, src)
        || return_type
            .as_deref()
            .map(|ty| ty.contains("IEnumerator"))
            .unwrap_or(false)
        || raw_body.contains("IEnumerator");
    let call_site_line = node.start_position().row as u32 + 1;

    emit_annotation_edges(
        edges,
        &uuid5_for(&fqn),
        node.child_by_field_name("type"),
        src,
        call_site_line,
    );
    emit_annotation_edges(
        edges,
        &uuid5_for(&fqn),
        node.child_by_field_name("parameters"),
        src,
        call_site_line,
    );

    Some(NodeRecord {
        id: uuid5_for(&fqn),
        node_type: "method".to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(truncate_chars(&signature, 300)),
        return_type,
        docstring: Some(String::new()),
        is_exported: Some(if has_public_modifier(node, src) { 1 } else { 0 }),
        is_async: Some(if is_async { 1 } else { 0 }),
        is_test: Some(0),
        raw_body: truncate_chars(&raw_body, 2000),
        skeleton_standard: Some(format!("{} {{\n    ...\n}}", signature)),
        skeleton_minimal: Some(format!("{}(...)", name_of(node, src).unwrap_or_default())),
        language: CSHARP_LANGUAGE_NAME.to_string(),
    })
}

fn build_property_node(
    node: Node,
    src: &[u8],
    file_path: &str,
    type_stack: &[TypeCtx],
    edges: &mut Vec<EdgeRecord>,
) -> Option<NodeRecord> {
    let name = name_of(node, src)?;
    let fqn = build_fqn(file_path, type_stack, Some(&name));
    let start_line = node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    let raw_body = text_of(node, src).to_string();
    let property_type = node
        .child_by_field_name("type")
        .map(|n| text_of(n, src).trim().to_string())
        .filter(|s| !s.is_empty());
    let signature = match property_type.as_deref() {
        Some(ty) => format!("{} {}", ty, name),
        None => name.clone(),
    };
    let source_id = uuid5_for(&fqn);

    emit_annotation_edges(
        edges,
        &source_id,
        node.child_by_field_name("type"),
        src,
        start_line,
    );

    Some(NodeRecord {
        id: source_id,
        node_type: "property".to_string(),
        name: name.clone(),
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature.clone()),
        return_type: property_type,
        docstring: Some(String::new()),
        is_exported: Some(if has_public_modifier(node, src) { 1 } else { 0 }),
        is_async: Some(0),
        is_test: Some(0),
        raw_body: truncate_chars(&raw_body, 2000),
        skeleton_standard: Some(format!("{} {{ get; set; }}", signature)),
        skeleton_minimal: Some(format!("{} {{ get; set; }}", name.clone())),
        language: CSHARP_LANGUAGE_NAME.to_string(),
    })
}

fn emit_base_edges(
    node: Node,
    src: &[u8],
    source_id: &str,
    kind: &str,
    edges: &mut Vec<EdgeRecord>,
) {
    let Some(base_list) = node.child_by_field_name("base_list") else {
        return;
    };

    let etype = if kind == "class" || kind == "struct" {
        "INHERITS"
    } else {
        "IMPLEMENTS"
    };

    let mut seen = HashSet::new();
    let mut cursor = base_list.walk();
    for child in base_list.children(&mut cursor) {
        let raw = text_of(child, src).trim();
        if raw.is_empty() || matches!(child.kind(), ":" | ",") {
            continue;
        }

        let target_name = simple_type_name(raw);
        if target_name.is_empty() || !seen.insert(target_name.clone()) {
            continue;
        }

        edges.push(EdgeRecord {
            source_id: source_id.to_string(),
            target_id: unresolved_name(&target_name),
            edge_type: etype.to_string(),
            target_name: Some(target_name),
            target_kind_hint: Some("type".to_string()),
            target_fqn_hint: None,
            call_site_line: Some(base_list.start_position().row as u32 + 1),
            confidence: 1.0,
        });
    }
}

fn emit_annotation_edges(
    edges: &mut Vec<EdgeRecord>,
    source_id: &str,
    node: Option<Node>,
    src: &[u8],
    call_site_line: u32,
) {
    let Some(node) = node else {
        return;
    };

    let mut seen = HashSet::new();
    for name in extract_type_names(text_of(node, src)) {
        if is_builtin_type(&name) || !seen.insert(name.clone()) {
            continue;
        }

        edges.push(EdgeRecord {
            source_id: source_id.to_string(),
            target_id: unresolved_name(&name),
            edge_type: "ANNOTATED_WITH".to_string(),
            target_name: Some(name),
            target_kind_hint: Some("type".to_string()),
            target_fqn_hint: None,
            call_site_line: Some(call_site_line),
            confidence: 1.0,
        });
    }
}

fn invocation_target_name(node: Node, src: &[u8]) -> Option<String> {
    let function_node = node.child_by_field_name("expression")?;
    let raw = text_of(function_node, src).trim();
    let last = raw
        .rsplit(|c| c == '.' || c == ':' || c == '>' || c == '<')
        .find(|segment| !segment.is_empty())?;
    Some(simple_type_name(last))
}

fn object_creation_target_name(node: Node, src: &[u8]) -> Option<String> {
    let type_node = node.child_by_field_name("type")?;
    let raw = text_of(type_node, src).trim();
    Some(simple_type_name(raw))
}

fn simple_type_name(text: &str) -> String {
    let trimmed = text.trim().trim_end_matches(';').trim_end_matches(',');
    let trimmed = trimmed.split('<').next().unwrap_or(trimmed);
    let trimmed = trimmed.rsplit("::").next().unwrap_or(trimmed);
    let trimmed = trimmed.rsplit('.').next().unwrap_or(trimmed);
    trimmed.trim().to_string()
}

fn extract_type_names(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for token in text.split(|c: char| !c.is_ascii_alphanumeric() && c != '_') {
        if token.len() > 1 && token.chars().next().is_some_and(|c| c.is_ascii_uppercase()) {
            out.push(token.to_string());
        }
    }
    out
}

fn is_builtin_type(name: &str) -> bool {
    BUILTIN_TYPES.iter().any(|candidate| candidate == &name)
}

fn build_fqn(file_path: &str, type_stack: &[TypeCtx], name: Option<&str>) -> String {
    let mut parts: Vec<&str> = type_stack.iter().map(|ctx| ctx.name.as_str()).collect();
    if let Some(name) = name {
        parts.push(name);
    }

    if parts.is_empty() {
        file_path.to_string()
    } else {
        format!("{}::{}", file_path, parts.join("::"))
    }
}

fn has_modifier(node: Node, src: &[u8], wanted: &str) -> bool {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "modifier" && text_of(child, src).trim() == wanted {
            return true;
        }
    }
    false
}

fn has_public_modifier(node: Node, src: &[u8]) -> bool {
    has_modifier(node, src, "public")
}

fn has_async_modifier(node: Node, src: &[u8]) -> bool {
    has_modifier(node, src, "async")
}

fn name_of(node: Node, src: &[u8]) -> Option<String> {
    node.child_by_field_name("name")
        .map(|n| text_of(n, src).to_string())
        .filter(|s| !s.is_empty())
}

fn text_before_body(text: &str, kind: &str, name: &str) -> String {
    if let Some(idx) = text.find('{') {
        text[..idx].trim().to_string()
    } else if let Some(idx) = text.find(';') {
        text[..idx].trim().to_string()
    } else if kind == "class" {
        format!("class {}", name)
    } else if kind == "interface" {
        format!("interface {}", name)
    } else if kind == "struct" {
        format!("struct {}", name)
    } else if kind == "enum" {
        format!("enum {}", name)
    } else {
        name.to_string()
    }
}

fn basename_without_cs_ext(file_path: &str) -> String {
    file_path
        .rsplit(|c| c == '/' || c == '\\')
        .next()
        .unwrap_or(file_path)
        .trim_end_matches(".cs")
        .to_string()
}

fn text_of<'a>(node: Node, src: &'a [u8]) -> &'a str {
    let start = node.start_byte();
    let end = node.end_byte();
    std::str::from_utf8(&src[start..end]).unwrap_or("")
}

fn truncate_chars(text: &str, max_len: usize) -> String {
    text.chars().take(max_len).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_node_is_first() {
        let src = "public class MyClass {}";
        let result = parse_csharp_file("Assets/MyClass.cs", src);
        assert!(!result.nodes.is_empty());
        let module = &result.nodes[0];
        assert_eq!(module.node_type, "module");
        assert_eq!(module.id, uuid5_for("Assets/MyClass.cs"));
        assert_eq!(module.name, "MyClass");
        assert_eq!(module.fqn, "Assets/MyClass.cs");
        assert_eq!(module.language, "csharp");
    }

    #[test]
    fn parses_unity_style_method_and_coroutine_flag() {
        let src = r#"
using UnityEngine;
public class MyGame : MonoBehaviour {
    void Start() {}
    IEnumerator MyCoroutine() { yield return null; }
}
"#;
        let result = parse_csharp_file("Assets/MyGame.cs", src);
        let start = result.nodes.iter().find(|n| n.name == "Start").unwrap();
        let coroutine = result
            .nodes
            .iter()
            .find(|n| n.name == "MyCoroutine")
            .unwrap();

        assert_eq!(start.node_type, "method");
        assert_eq!(coroutine.is_async, Some(1));
    }

    #[test]
    fn edge_source_ids_reference_existing_nodes() {
        let src = r#"
using System.Collections.Generic;
public class A : B {
    public void Foo() {
        Bar();
        new Baz();
    }

    public List<string> Values { get; set; }
}
"#;
        let result = parse_csharp_file("A.cs", src);
        let node_ids: HashSet<_> = result.nodes.iter().map(|n| n.id.as_str()).collect();
        assert!(result
            .edges
            .iter()
            .all(|edge| node_ids.contains(edge.source_id.as_str())));
        assert!(result.edges.iter().any(|edge| edge.edge_type == "CALLS"));
    }
}
