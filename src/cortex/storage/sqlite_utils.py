import os

def to_rel_path(full_path: str, workspace: str) -> str:
    """절대 경로를 워크스페이스 기준 상대 경로(ROOT/...)로 변환"""
    if not full_path or not workspace:
        return full_path
    try:
        rel = os.path.relpath(full_path, workspace)
        return os.path.join("ROOT", rel).replace("\\", "/")
    except Exception:
        return full_path

def to_abs_path(rel_path: str, workspace: str) -> str:
    """ROOT/... 형식의 상대 경로를 현재 환경의 절대 경로로 복원"""
    if not rel_path or not workspace or not rel_path.startswith("ROOT"):
        return rel_path
    return os.path.abspath(os.path.join(workspace, rel_path.replace("ROOT/", "").replace("ROOT\\", "")))
