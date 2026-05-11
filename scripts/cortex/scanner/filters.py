import os
import fnmatch
from pathlib import Path

def should_include(path: str, workspace: str, settings: dict) -> bool:
    """파일이 인덱싱 범위에 포함되는지 확인 (Whitelist 우선)"""
    rules = settings.get("indexing_rules", {})
    rel = os.path.relpath(path, workspace)

    def _matches(pattern: str) -> bool:
        """** 포함 패턴은 pathlib.PurePath.match() 사용, 단순 패턴은 fnmatch 사용."""
        if "**" in pattern:
            return Path(rel).match(pattern)
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern)

    # 1. 화이트리스트 파일 체크
    whitelist = rules.get("config_whitelist", [])
    for pattern in whitelist:
        if _matches(pattern):
            return True

    # 2. 포함 경로 체크
    # 기본값 "**": settings.yaml 없는 워크스페이스에서 전체 파일 포함 (빈 프로젝트 보호)
    includes = rules.get("include_paths", ["**"])
    for pattern in includes:
        if _matches(pattern):
            return True

    # 3. 모듈별 경로 체크
    modules = rules.get("modules", {})
    if isinstance(modules, dict):
        for mod_name, mod_paths in modules.items():
            for m_path in mod_paths:
                if rel.startswith(m_path) or fnmatch.fnmatch(rel, m_path):
                    return True

    return False

def get_module_name(rel_path: str, settings: dict) -> str:
    """경로 기반 모듈명 판단"""
    modules = settings.get("indexing_rules", {}).get("modules", {})
    if isinstance(modules, dict):
        for mod_name, mod_paths in modules.items():
            for m_path in mod_paths:
                if f"{m_path}{os.sep}" in f"{rel_path}{os.sep}" or rel_path.endswith(m_path):
                    return mod_name
    parts = rel_path.split(os.sep)
    return parts[0] if len(parts) > 1 else "root"
