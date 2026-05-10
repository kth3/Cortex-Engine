"""
파일 또는 노드의 스켈레톤(시그니처 + 독스트링)을 생성하여 토큰을 절약합니다.
"""
import os

from cortex.parsers import registry as parser_registry


def get_parser_internal(file_path: str):
    """확장자에 맞는 파서 함수 반환 (레지스트리 활용)"""
    ext = os.path.splitext(file_path)[1]
    _, parser_func = parser_registry.parsers.get(ext, (None, None))
    return parser_func


def get_node_skeleton(node_dict, detail="standard"):
    """단일 노드의 스켈레톤 생성"""
    signature = node_dict.get("signature", "")
    docstring = (
        node_dict.get("raw_body", "").strip().split("\n")[0]
        if "raw_body" in node_dict
        else ""
    )

    if detail == "minimal":
        return signature
    if detail == "standard":
        if (
            docstring.startswith('"""')
            or docstring.startswith("'''")
            or docstring.startswith("/*")
            or docstring.startswith("//")
        ):
            return f"{signature}\n    {docstring}"
        return signature

    body = node_dict.get("raw_body", "")
    lines = body.split("\n")
    return "\n".join(lines[:5]) + " ... (truncated)"


def generate_file_skeleton(nodes, detail="standard"):
    """파일 내의 모든 노드를 순서대로 스켈레톤화하여 결합"""
    sorted_nodes = sorted(nodes, key=lambda x: x.get("start_line", 0))
    parts = []
    for node in sorted_nodes:
        skel = get_node_skeleton(node, detail)
        if skel:
            parts.append(str(skel))
    return "\n\n".join(parts)


def generate_skeleton(workspace, file_path, detail="standard"):
    parser_func = get_parser_internal(file_path)

    if not parser_func:
        return f"No parser found for: {file_path}"

    abs_path = os.path.join(workspace, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_path):
        return f"File not found: {abs_path}"

    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    result = parser_func(file_path, code)
    nodes = result.get("nodes", [])
    return generate_file_skeleton(nodes, detail)
