"""
Pure-Context Python 파서
표준 라이브러리 ast 모듈을 사용하여 Python 소스를 노드로 변환
"""
import ast
import uuid

def parse_python_file(file_path: str, source: str) -> dict:
    """Python 파일을 파싱하여 노드와 엣지를 추출합니다."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return {"nodes": [], "edges": []}
    
    nodes = []
    edges = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls_node = _extract_class(node, file_path, source)
            nodes.append(cls_node)
            
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_node = _extract_method(item, file_path, source, parent_class=node.name)
                    nodes.append(method_node)
                    # 클래스 → 메서드 엣지
                    edges.append({
                        "source_id": cls_node["id"],
                        "target_id": method_node["id"],
                        "type": "CONTAINS",
                        "call_site_line": item.lineno
                    })
                    # 메서드 내부 호출 추적
                    edges.extend(_extract_calls(item, method_node["id"], source))
        
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 트리 최상위 함수 (클래스 내부가 아닌)
            if not _is_method(node, tree):
                func_node = _extract_function(node, file_path, source)
                nodes.append(func_node)
                edges.extend(_extract_calls(node, func_node["id"], source))
    
    return {"nodes": nodes, "edges": edges}

def _extract_class(node: ast.ClassDef, file_path: str, source: str) -> dict:
    """클래스 노드 추출"""
    end_line = _get_end_line(node)
    body = _get_source_segment(source, node.lineno, end_line)
    bases = ", ".join(
        ast.unparse(b) if hasattr(ast, 'unparse') else getattr(b, 'id', '?')
        for b in node.bases
    )
    sig = f"class {node.name}({bases}):" if bases else f"class {node.name}:"
    docstring = ast.get_docstring(node) or ""
    
    skeleton_std = sig + "\n"
    if docstring:
        skeleton_std += f'    """{docstring}"""\n'
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async " if isinstance(item, ast.AsyncFunctionDef) else ""
            msig = f"    {prefix}def {item.name}({_get_args_str(item)}):"
            mdoc = ast.get_docstring(item)
            skeleton_std += msig + "\n"
            if mdoc:
                skeleton_std += f'        """{_truncate(mdoc, 80)}"""\n'
            skeleton_std += "        ...\n"
    
    skeleton_min = f"{sig}\n    ...  # {len(node.body)} members"
    
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{node.name}")),
        "type": "class",
        "name": node.name,
        "fqn": f"{file_path}::{node.name}",
        "file_path": file_path,
        "start_line": node.lineno,
        "end_line": end_line,
        "signature": sig,
        "return_type": None,
        "docstring": _truncate(docstring, 200),
        "is_exported": not node.name.startswith("_"),
        "is_async": 0,
        "is_test": node.name.startswith("Test") or node.name.endswith("Test"),
        "raw_body": body,
        "skeleton_standard": skeleton_std,
        "skeleton_minimal": skeleton_min,
        "language": "python"
    }

def _extract_method(node, file_path: str, source: str, parent_class: str) -> dict:
    """메서드 노드 추출"""
    return _extract_func_like(node, file_path, source, "method", parent_class)

def _extract_function(node, file_path: str, source: str) -> dict:
    """최상위 함수 노드 추출"""
    return _extract_func_like(node, file_path, source, "function", None)

def _extract_func_like(node, file_path: str, source: str, ntype: str, parent: str | None) -> dict:
    """함수/메서드 공통 추출 로직"""
    end_line = _get_end_line(node)
    body = _get_source_segment(source, node.lineno, end_line)
    is_async = isinstance(node, ast.AsyncFunctionDef)
    prefix = "async " if is_async else ""
    args_str = _get_args_str(node)
    ret = ""
    if node.returns:
        ret = ast.unparse(node.returns) if hasattr(ast, 'unparse') else "?"
    sig = f"{prefix}def {node.name}({args_str})" + (f" -> {ret}" if ret else "") + ":"
    docstring = ast.get_docstring(node) or ""
    
    fqn_parts = [file_path]
    if parent:
        fqn_parts.append(parent)
    fqn_parts.append(node.name)
    fqn = "::".join(fqn_parts)
    
    line_count = end_line - node.lineno + 1
    skeleton_std = f"{sig}\n"
    if docstring:
        skeleton_std += f'    """{_truncate(docstring, 80)}"""\n'
    skeleton_std += f"\n    ...  # [{line_count} lines — implementation hidden]"
    skeleton_min = f"{prefix}def {node.name}(\n    ...  # {line_count} lines"
    
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
        "type": ntype,
        "name": node.name,
        "fqn": fqn,
        "file_path": file_path,
        "start_line": node.lineno,
        "end_line": end_line,
        "signature": sig,
        "return_type": ret or None,
        "docstring": _truncate(docstring, 200),
        "is_exported": not node.name.startswith("_"),
        "is_async": int(is_async),
        "is_test": node.name.startswith("test_") or node.name.startswith("test"),
        "raw_body": body,
        "skeleton_standard": skeleton_std,
        "skeleton_minimal": skeleton_min,
        "language": "python"
    }

def _extract_calls(func_node, source_id: str, source: str) -> list:
    """함수 내부의 호출 관계를 추출 (CALLS 엣지)"""
    edges = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            target_name = None
            if isinstance(node.func, ast.Attribute):
                target_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                target_name = node.func.id
            
            if target_name and not target_name.startswith("_"):
                # 실제 target_id는 인덱싱 후 FQN 해석 단계에서 연결
                edges.append({
                    "source_id": source_id,
                    "target_id": f"__unresolved__::{target_name}",
                    "type": "CALLS",
                    "call_site_line": getattr(node, 'lineno', None)
                })
    return edges

# ==============================================================================
# 유틸리티
# ==============================================================================

def _is_method(node, tree) -> bool:
    """노드가 클래스 내부 메서드인지 확인"""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for item in parent.body:
                if item is node:
                    return True
    return False

def _get_end_line(node) -> int:
    """노드의 마지막 줄 번호"""
    return getattr(node, 'end_lineno', node.lineno) or node.lineno

def _get_args_str(node) -> str:
    """함수 인자 문자열 생성"""
    args = []
    for a in node.args.args:
        name = a.arg
        if name == "self" or name == "cls":
            args.append(name)
        elif a.annotation:
            ann = ast.unparse(a.annotation) if hasattr(ast, 'unparse') else "?"
            args.append(f"{name}: {ann}")
        else:
            args.append(name)
    return ", ".join(args)

def _get_source_segment(source: str, start: int, end: int) -> str:
    """소스에서 특정 줄 범위 추출"""
    lines = source.splitlines()
    segment = lines[start - 1:end]
    return "\n".join(segment)

def _truncate(text: str, max_len: int) -> str:
    """텍스트를 최대 길이로 자름"""
    if not text:
        return ""
    first_line = text.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line
