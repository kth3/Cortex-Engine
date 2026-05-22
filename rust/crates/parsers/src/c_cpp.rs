//! C/C++ 파서 — Python `parsers/c_parser.py` 대응 (tree-sitter-cpp 기반).
//!
//! C와 C++ 모두 tree-sitter-cpp로 처리 (C++ 문법이 C의 superset).
//! 추출 대상: class / struct / enum + function + 함수형 매크로(#define X(args) body).
//! Python regex 파서를 tree-sitter로 교체 (정확도 향상이 본래 목적).

use tree_sitter::{Node, Parser};

use crate::common::{truncate, uuid5_for, EdgeRecord, NodeRecord, ParseResult};

pub fn parse_c_file(file_path: &str, source: &str) -> ParseResult {
    let lang = if file_path
        .to_ascii_lowercase()
        .ends_with(|c| matches!(c, 'p' | 'x' | 'c'))
        && (file_path.ends_with(".cpp")
            || file_path.ends_with(".hpp")
            || file_path.ends_with(".cc")
            || file_path.ends_with(".cxx"))
    {
        "cpp"
    } else {
        "c"
    };

    let mut parser = Parser::new();
    if parser
        .set_language(&tree_sitter_cpp::LANGUAGE.into())
        .is_err()
    {
        return ParseResult::default();
    }

    let Some(tree) = parser.parse(source, None) else {
        return ParseResult::default();
    };
    let root = tree.root_node();
    let bytes = source.as_bytes();

    let mut nodes: Vec<NodeRecord> = Vec::new();
    let mut edges: Vec<EdgeRecord> = Vec::new();

    walk(root, bytes, file_path, lang, &mut nodes, &mut edges);

    ParseResult { nodes, edges }
}

fn walk(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang: &str,
    nodes: &mut Vec<NodeRecord>,
    edges: &mut Vec<EdgeRecord>,
) {
    match node.kind() {
        "class_specifier" => {
            if let Some(nr) = build_class_like(node, src, file_path, lang, "class") {
                nodes.push(nr);
            }
        }
        "struct_specifier" => {
            if let Some(nr) = build_class_like(node, src, file_path, lang, "struct") {
                nodes.push(nr);
            }
        }
        "enum_specifier" => {
            if let Some(nr) = build_enum(node, src, file_path, lang) {
                nodes.push(nr);
            }
        }
        "function_definition" => {
            if let Some(nr) = build_function(node, src, file_path, lang) {
                nodes.push(nr);
            }
            // 함수 본문 안으로는 내려가지 않음 (내부 람다/구조체는 노이즈)
            return;
        }
        "preproc_function_def" => {
            if let Some(nr) = build_macro(node, src, file_path, lang) {
                nodes.push(nr);
            }
            return;
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(child, src, file_path, lang, nodes, edges);
    }
}

fn text_of<'a>(node: Node, src: &'a [u8]) -> &'a str {
    let s = node.start_byte();
    let e = node.end_byte();
    std::str::from_utf8(&src[s..e]).unwrap_or("")
}

fn find_block_comment_above(node: Node, src: &[u8]) -> String {
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
    if let Some(c) = prev {
        if c.kind() == "comment" {
            let raw = text_of(c, src);
            // Doxygen `/** ... */`
            if raw.starts_with("/**") {
                let inner = raw.trim_start_matches("/**").trim_end_matches("*/");
                let mut lines: Vec<String> = inner
                    .lines()
                    .map(|l| {
                        l.trim_start()
                            .trim_start_matches('*')
                            .trim_start()
                            .to_string()
                    })
                    .collect();
                if let Some(f) = lines.first() {
                    if f.is_empty() {
                        lines.remove(0);
                    }
                }
                let joined = lines.join("\n");
                let before_at: &str = joined.split("\n@").next().unwrap_or("");
                return before_at.trim().to_string();
            }
            // 줄 주석은 무시 (Python은 // 연속 라인을 수집하나 단순화)
        }
    }
    String::new()
}

fn build_class_like(
    node: Node,
    src: &[u8],
    file_path: &str,
    lang: &str,
    kind: &str,
) -> Option<NodeRecord> {
    let name_node = node.child_by_field_name("name")?;
    // forward declaration (예: `class UBoxComponent;`)은 body가 없음 — 인덱싱 제외.
    // Python regex 파서는 `{` 가 있어야 매칭되므로 동일하게 동작.
    node.child_by_field_name("body")?;
    let name = text_of(name_node, src).to_string();
    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;

    // base_class_clause
    let bases = node
        .child_by_field_name("base_class_clause")
        .map(|n| text_of(n, src).trim().to_string())
        .unwrap_or_default();

    let signature = if bases.is_empty() {
        format!("{} {}", kind, name)
    } else {
        format!("{} {} {}", kind, name, bases)
    };

    let fqn = format!("{}::{}", file_path, name);
    let id = uuid5_for(&fqn);
    let docstring = find_block_comment_above(node, src);
    let raw_body = text_of(node, src).to_string();
    let skel_std = format!("{} {{\n    ...\n}};", signature);
    let skel_min = format!("{} {{ ... }};", signature);
    let is_test = name.ends_with("Test") || name.starts_with("Test");

    Some(NodeRecord {
        id,
        node_type: kind.to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature),
        return_type: None,
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(if is_test { 1 } else { 0 }),
        raw_body,
        skeleton_standard: Some(skel_std),
        skeleton_minimal: Some(skel_min),
        language: lang.to_string(),
    })
}

fn build_enum(node: Node, src: &[u8], file_path: &str, lang: &str) -> Option<NodeRecord> {
    let name_node = node.child_by_field_name("name")?;
    let name = text_of(name_node, src).to_string();
    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;

    // enum class 여부
    let raw = text_of(node, src);
    let kind = if raw.starts_with("enum class") || raw.starts_with("enum struct") {
        "enum class"
    } else {
        "enum"
    };
    let signature = format!("{} {}", kind, name);

    let fqn = format!("{}::{}", file_path, name);
    let id = uuid5_for(&fqn);
    let docstring = find_block_comment_above(node, src);
    let raw_body = raw.to_string();

    Some(NodeRecord {
        id,
        node_type: "enum".to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature.clone()),
        return_type: None,
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body,
        skeleton_standard: Some(format!("{} {{ ... }};", signature)),
        skeleton_minimal: Some(format!("{} {{ ... }};", signature)),
        language: lang.to_string(),
    })
}

fn build_function(node: Node, src: &[u8], file_path: &str, lang: &str) -> Option<NodeRecord> {
    // function_definition → declarator (function_declarator) → declarator (identifier or qualified)
    let declarator = node.child_by_field_name("declarator")?;
    let (name, params_text) = extract_func_name_and_params(declarator, src)?;

    // 키워드 필터 (Python 동등)
    if matches!(
        name.as_str(),
        "if" | "for" | "while" | "switch" | "return" | "catch" | "sizeof" | "typeof"
    ) {
        return None;
    }

    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;

    // return type
    let return_type = node
        .child_by_field_name("type")
        .map(|n| text_of(n, src).trim().to_string())
        .unwrap_or_default();

    // 본문 외 헤더만 추출하여 signature
    let signature = if return_type.is_empty() {
        format!("{}{}", name, params_text)
    } else {
        format!("{} {}{}", return_type, name, params_text)
    };

    // 단순 이름 (namespace 제거)
    let simple_name = name.rsplit("::").next().unwrap_or(&name).to_string();

    let fqn = format!("{}::{}", file_path, name);
    let id = uuid5_for(&fqn);
    let docstring = find_block_comment_above(node, src);
    let raw_body = text_of(node, src).to_string();
    let skel_std = format!("{} {{\n    ...\n}}", signature);
    let skel_min = format!("{}(...)", simple_name);
    let is_exported = !simple_name.starts_with('_');
    let is_test = simple_name.to_lowercase().contains("test");

    Some(NodeRecord {
        id,
        node_type: "function".to_string(),
        name: simple_name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature),
        return_type: if return_type.is_empty() {
            None
        } else {
            Some(return_type)
        },
        docstring: Some(truncate(&docstring, 200)),
        is_exported: Some(if is_exported { 1 } else { 0 }),
        is_async: Some(0),
        is_test: Some(if is_test { 1 } else { 0 }),
        raw_body,
        skeleton_standard: Some(skel_std),
        skeleton_minimal: Some(skel_min),
        language: lang.to_string(),
    })
}

/// declarator에서 함수 이름과 파라미터 텍스트 추출.
fn extract_func_name_and_params(decl: Node, src: &[u8]) -> Option<(String, String)> {
    match decl.kind() {
        "function_declarator" => {
            let inner_decl = decl.child_by_field_name("declarator")?;
            let name = text_of(inner_decl, src).to_string();
            let params = decl
                .child_by_field_name("parameters")
                .map(|n| text_of(n, src).to_string())
                .unwrap_or_else(|| "()".to_string());
            Some((name, params))
        }
        // pointer_declarator / reference_declarator 등은 안쪽 declarator로 내려감
        "pointer_declarator" | "reference_declarator" | "init_declarator" => {
            let inner = decl.child_by_field_name("declarator")?;
            extract_func_name_and_params(inner, src)
        }
        _ => None,
    }
}

fn build_macro(node: Node, src: &[u8], file_path: &str, lang: &str) -> Option<NodeRecord> {
    let name_node = node.child_by_field_name("name")?;
    let name = text_of(name_node, src).to_string();
    let params = node
        .child_by_field_name("parameters")
        .map(|n| text_of(n, src).to_string())
        .unwrap_or_else(|| "()".to_string());
    let start_line = (node.start_position().row + 1) as u32;
    let end_line = (node.end_position().row + 1) as u32;
    let raw_body = text_of(node, src).to_string();
    let fqn = format!("{}::#{}", file_path, name);
    let id = uuid5_for(&fqn);
    let signature = format!("#define {}{}", name, params);

    Some(NodeRecord {
        id,
        node_type: "macro".to_string(),
        name,
        fqn,
        file_path: file_path.to_string(),
        start_line,
        end_line,
        signature: Some(signature.clone()),
        return_type: None,
        docstring: Some(String::new()),
        is_exported: Some(1),
        is_async: Some(0),
        is_test: Some(0),
        raw_body,
        skeleton_standard: Some(format!("{} ...", signature)),
        skeleton_minimal: Some(signature),
        language: lang.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_c_function() {
        let src = "int add(int a, int b) { return a + b; }";
        let r = parse_c_file("a.c", src);
        let f = r.nodes.iter().find(|n| n.node_type == "function").unwrap();
        assert_eq!(f.name, "add");
        assert_eq!(f.return_type.as_deref(), Some("int"));
        assert_eq!(f.language, "c");
    }

    #[test]
    fn parse_cpp_class_with_method() {
        let src = "class Foo {\npublic:\n    void bar() { return; }\n};";
        let r = parse_c_file("a.cpp", src);
        let cls = r.nodes.iter().find(|n| n.node_type == "class").unwrap();
        assert_eq!(cls.name, "Foo");
        let m = r.nodes.iter().find(|n| n.node_type == "function").unwrap();
        assert_eq!(m.name, "bar");
        assert_eq!(m.language, "cpp");
    }

    #[test]
    fn parse_struct() {
        let src = "struct Point { int x; int y; };";
        let r = parse_c_file("a.h", src);
        let s = r.nodes.iter().find(|n| n.node_type == "struct").unwrap();
        assert_eq!(s.name, "Point");
    }

    #[test]
    fn parse_enum() {
        let src = "enum Color { RED, GREEN, BLUE };";
        let r = parse_c_file("a.c", src);
        let e = r.nodes.iter().find(|n| n.node_type == "enum").unwrap();
        assert_eq!(e.name, "Color");
    }

    #[test]
    fn parse_macro() {
        let src = "#define MAX(a,b) ((a) > (b) ? (a) : (b))\n";
        let r = parse_c_file("a.h", src);
        let m = r.nodes.iter().find(|n| n.node_type == "macro").unwrap();
        assert_eq!(m.name, "MAX");
    }
}
