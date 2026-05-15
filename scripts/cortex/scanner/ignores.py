import os
import fnmatch

DEFAULT_IGNORES = [
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", "build", ".gradle", ".idea", ".vscode",
    ".cortex", "target", ".next", "*.min.js", "*.min.css",
    "*.pyc", "*.class", "*.o", "*.obj", "*.exe", "*.out",
    "Library", "Temp", "Logs", "obj",  # Unity 캐시
]

def load_gitignore(workspace: str) -> list:
    """프로젝트의 .gitignore 패턴 로드"""
    patterns = list(DEFAULT_IGNORES)
    gitignore_path = os.path.join(workspace, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line.strip("/"))
        except Exception:
            pass
    return patterns

def should_ignore(path: str, ignore_patterns: list, workspace: str) -> bool:
    """파일/디렉토리가 무시 대상인지 확인"""
    rel = os.path.relpath(path, workspace)
    parts = rel.split(os.sep)
    for part in parts:
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False
