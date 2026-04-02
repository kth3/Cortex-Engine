"""
파일 또는 노드의 스켈레톤(시그니처 + 독스트링)을 생성하여 토큰을 절약합니다.
"""
import os
import importlib

# ==============================================================================
# 인덱서 의존성 제거 및 런타임 수복을 위한 로직 (Inlining)
# ==============================================================================
def get_parser_internal(file_path: str):
    """확장자에 맞는 파서 함수 반환 (내부 수복 버전)"""
    ext = os.path.splitext(file_path)[1]
    if ext == ".java":
        try:
            import cortex.parsers.java_parser as java_parser
            importlib.reload(java_parser)
            return java_parser.parse_java_file
        except ImportError:
            return None
    return None

def get_node_skeleton(node_dict, detail="standard"):
    """
    단일 노드의 스켈레톤 생성
    """
    signature = node_dict.get("signature", "")
    docstring = node_dict.get("raw_body", "").strip().split("\n")[0] if "raw_body" in node_dict else ""
    
    if detail == "minimal":
        return signature
    elif detail == "standard":
        if docstring.startswith('"""') or docstring.startswith("'''") or docstring.startswith("/*") or docstring.startswith("//"):
             return f"{signature}\n    {docstring}"
        return signature
    else:
        body = node_dict.get("raw_body", "")
        lines = body.split("\n")
        return "\n".join(lines[:5]) + " ... (truncated)"

def generate_file_skeleton(nodes, detail="standard"):
    """
    파일 내의 모든 노드를 순서대로 스켈레톤화하여 결합
    """
    sorted_nodes = sorted(nodes, key=lambda x: x.get("start_line", 0))
    parts = []
    for node in sorted_nodes:
        skel = get_node_skeleton(node, detail)
        if skel:
            parts.append(str(skel))
    return "\n\n".join(parts)

# MCP Server Alias (Called by cortex_mcp.py)
def generate_skeleton(workspace, file_path, detail="standard"):
    # 외부 모듈 의존성 없이 내부 로직 사용
    parser_func = get_parser_internal(file_path)
    
    if not parser_func:
        return f"No parser found for: {file_path}"
        
    abs_path = os.path.join(workspace, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_path):
        return f"File not found: {abs_path}"

    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
        code = f.read()
        
    # 파서 함수 직접 호출 (dict 형식 반환 대응)
    result = parser_func(file_path, code)
    nodes = result.get("nodes", [])
    return generate_file_skeleton(nodes, detail)
