from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    for node in nodes:
        if "type" not in node and "node_type" in node:
            node["type"] = node.pop("node_type")
        node.setdefault("signature", None)
        node.setdefault("return_type", None)
        node.setdefault("docstring", None)
        node.setdefault("is_exported", None)
        node.setdefault("is_async", None)
        node.setdefault("is_test", None)
        node.setdefault("raw_body", "")
        node.setdefault("skeleton_standard", None)
        node.setdefault("skeleton_minimal", None)
    for edge in edges:
        if "edge_type" in edge and "type" not in edge:
            edge["type"] = edge.pop("edge_type")
        edge.setdefault("target_name", None)
        edge.setdefault("target_kind_hint", None)
        edge.setdefault("target_fqn_hint", None)
        edge.setdefault("call_site_line", None)
        edge.setdefault("confidence", 1.0)
    return {"nodes": nodes, "edges": edges}


def _parse(path: str, source: str | None, file: str | None) -> dict[str, Any]:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        from cortex.parsers.pdf_parser import parse_pdf_file
        return parse_pdf_file(file or path)
    if source is None:
        if not file:
            raise ValueError("--file or stdin source is required")
        source = Path(file).read_text(encoding="utf-8")

    if ext == ".py":
        from cortex.parsers.treesitter_python_parser import parse_python_file
        return parse_python_file(path, source)
    if ext == ".cs":
        from cortex.parsers.treesitter_cs_parser import parse_csharp_file
        return parse_csharp_file(path, source)
    if ext == ".java":
        from cortex.parsers.treesitter_java_parser import parse_java_file
        return parse_java_file(path, source)
    if ext in {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp"}:
        from cortex.parsers.treesitter_c_parser import parse_c_file
        return parse_c_file(path, source)
    if ext in {".ts", ".tsx", ".js", ".jsx"}:
        from cortex.parsers.treesitter_ts_parser import parse_ts_file
        language = {
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
        }.get(ext, "typescript")
        return parse_ts_file(path, source, language)
    if ext in {".md", ".markdown", ".html", ".htm", ".css"}:
        from cortex.parsers.markdown_parser import parse_markdown_file
        return parse_markdown_file(path, source)
    raise ValueError(f"No parser found for: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cortex.parsers.bridge")
    parser.add_argument("--path", required=True)
    parser.add_argument("--file")
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args(argv)
    source = sys.stdin.read() if args.stdin else None
    result = _normalize_result(_parse(args.path, source, args.file))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
