import os
from pathlib import Path
from cortex.config.settings import load_settings
from cortex.scanner.ignores import load_gitignore, should_ignore
from cortex.scanner.filters import should_include
from cortex.paths import resolve_cortex_home

def get_index_roots(workspace: str, settings: dict) -> list[str]:
    """settings의 index_roots를 워크스페이스 상대 경로 목록으로 정규화."""
    rules = settings.get("indexing_rules", {})
    roots = rules.get("index_roots")
    if roots is None:
        roots = ["."]
    if isinstance(roots, str):
        roots = [roots]

    workspace_path = Path(workspace).resolve()
    normalized = []
    for root in roots or []:
        if not root:
            continue
        candidate = (workspace_path / root).resolve()
        try:
            rel = candidate.relative_to(workspace_path)
        except ValueError:
            continue
        rel_text = "." if str(rel) == "." else str(rel).replace("\\", "/")
        if rel_text not in normalized:
            normalized.append(rel_text)
    return normalized

def _iter_index_root_files(workspace: str, root_rel: str, supported_extensions: dict, ignore_patterns: list):
    workspace_path = Path(workspace).resolve()
    root_path = workspace_path if root_rel == "." else (workspace_path / root_rel).resolve()
    if not root_path.exists():
        return
    if root_path.is_file():
        if root_path.suffix in supported_extensions and not should_ignore(str(root_path), ignore_patterns, workspace):
            yield str(root_path)
        return
    for root, dirs, filenames in os.walk(root_path):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns, workspace)]
        for fname in filenames:
            full_path = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1]
            if ext in supported_extensions and not should_ignore(full_path, ignore_patterns, workspace):
                yield full_path

def scan_files(workspace: str, supported_extensions: dict, settings_override: dict | None = None) -> list:
    """지능형 필터링을 적용하여 인덱싱할 파일 목록 확보"""
    workspace = str(Path(workspace).resolve())
    settings = settings_override or load_settings(workspace)
    ignore_patterns = load_gitignore(workspace)

    # [배포 대응] settings.yaml의 exclude_paths를 ignore_patterns에 추가
    rules = settings.get("indexing_rules", {})
    extra_excludes = rules.get("exclude_paths", [])
    if extra_excludes:
        ignore_patterns.extend([p.strip("/") for p in extra_excludes if p.strip()])

    files = []

    # 1. 명시된 인덱싱 루트만 스캔
    for root_rel in get_index_roots(workspace, settings):
        for full_path in _iter_index_root_files(workspace, root_rel, supported_extensions, ignore_patterns):
            if should_include(full_path, workspace, settings):
                files.append(os.path.relpath(full_path, workspace))
                        
    # 2. Cortex 내부 규칙, 프로토콜, 설계 문서 강제 포함
    # [수정] knowledge 하위 폴더(resources/examples/skills)는 nodes 테이블 인덱싱에서 제외.
    # 이유: 1,500+ 외부 문서가 nodes 테이블에 Skill 노드로 쌓이면 pc_capsule 검색 결과를
    #       오염시켜 프로젝트 실제 코드가 뒤로 밀림 (RRF Hub 편향 현상).
    # 대안: _sync_rules_to_memories()가 memories 테이블에 텍스트로 동기화하므로
    #       pc_memory_search_knowledge(category: skill/resource/example)로 계속 검색 가능.
    cortex_home = resolve_cortex_home(workspace)
    home_rel = os.path.relpath(str(cortex_home), workspace)
    agent_docs = [
        os.path.join(home_rel, "rules"),
        # knowledge/resources, examples, skills는 nodes 제외: memories 테이블로 검색 가능
        os.path.join(home_rel, "docs"),          # ADR 등 설계 문서
    ]
    for doc_dir in agent_docs:
        abs_doc_dir = os.path.join(workspace, doc_dir)
        if os.path.exists(abs_doc_dir):
            for path in Path(abs_doc_dir).rglob("*.md"):
                files.append(os.path.relpath(str(path), workspace))

    # Cortex 엔진 및 운영 스크립트 강제 포함 (메타개발 지원)
    cortex_scripts_dir = cortex_home / "scripts"
    if cortex_scripts_dir.exists():
        for path in cortex_scripts_dir.rglob("*.py"):
            spath = str(path)
            if any(x in spath for x in ["__pycache__", ".venv", "site-packages"]):
                continue
            files.append(os.path.relpath(spath, workspace))

    return sorted(list(set(files)))
