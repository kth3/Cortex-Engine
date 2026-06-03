"""Shared helpers for Python tree-sitter parsers."""
from __future__ import annotations

import re
import uuid
from tree_sitter import Language, Parser

try:
    import tree_sitter_c_sharp as _ts_c_sharp
    CS_LANGUAGE = Language(_ts_c_sharp.language())
except ImportError:
    CS_LANGUAGE = None

try:
    import tree_sitter_typescript as _ts_typescript
    TS_LANGUAGE = Language(_ts_typescript.language_typescript())
    TSX_LANGUAGE = Language(_ts_typescript.language_tsx())
except ImportError:
    TS_LANGUAGE = None
    TSX_LANGUAGE = None


def make_id(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def truncate(text: str | None, mx: int) -> str:
    text = text or ""
    first = text.splitlines()[0].strip() if text else ""
    return first[:mx]


def txt(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node else ""


def name_of(node) -> str:
    n = node.child_by_field_name("name")
    return txt(n) if n else ""


def build_fqn(node, file_path: str) -> str:
    parts = []
    cur = node
    while cur is not None:
        if cur.type in (
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
            "method_declaration",
            "constructor_declaration",
            "property_declaration",
            "method_definition",
        ):
            name = name_of(cur)
            if name:
                parts.append(name)
        cur = cur.parent
    return f"{file_path}::{'::'.join(reversed(parts))}" if parts else file_path


def parser_for(language) -> Parser:
    parser = Parser()
    parser.language = Language(language())
    return parser


def node_line(node) -> int:
    return node.start_point[0] + 1


def node_end_line(node) -> int:
    return node.end_point[0] + 1


def node_text(source: str, node) -> str:
    return source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def unresolved_name(name: str) -> str:
    return f"__unresolved__::{name}"


def unresolved_fqn(fqn: str) -> str:
    return f"__unresolved_fqn__::{fqn}"


def simple_name(value: str) -> str:
    return re.split(r"[./:]", value.strip())[-1]


def extract_type_names(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\b", text or "")]


def base_node(
    *, file_path: str, node_type: str, name: str, fqn: str, start_line: int,
    end_line: int, language: str, signature: str | None = None,
    return_type: str | None = None, docstring: str = "", raw_body: str = "",
    is_exported=True, is_async=False, is_test=False,
    skeleton_standard: str | None = None, skeleton_minimal: str | None = None,
) -> dict:
    return {
        "id": make_id(fqn),
        "type": node_type,
        "name": name,
        "fqn": fqn,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "signature": signature,
        "return_type": return_type,
        "docstring": truncate(docstring, 200),
        "is_exported": int(bool(is_exported)),
        "is_async": int(bool(is_async)),
        "is_test": int(bool(is_test)),
        "raw_body": raw_body,
        "skeleton_standard": skeleton_standard,
        "skeleton_minimal": skeleton_minimal,
        "language": language,
    }


def edge(source_id: str, target_id: str, edge_type: str, *, target_name=None,
         target_kind_hint=None, target_fqn_hint=None, call_site_line=None,
         confidence=1.0) -> dict:
    return {
        "source_id": source_id,
        "target_id": target_id,
        "type": edge_type,
        "target_name": target_name,
        "target_kind_hint": target_kind_hint,
        "target_fqn_hint": target_fqn_hint,
        "call_site_line": call_site_line,
        "confidence": confidence,
    }
