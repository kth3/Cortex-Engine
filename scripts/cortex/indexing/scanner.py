import fnmatch
import json
import os
import subprocess
from pathlib import Path

from cortex.config.settings import load_settings
from cortex.indexing.index_roots import normalize_configured_index_roots
from cortex.logger import get_logger
from cortex.paths import resolve_cortex_home
from cortex.runtime.paths import ensure_rust_watcher_binary

log = get_logger("scanner")

DEFAULT_IGNORES = [
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".gradle",
    ".idea",
    ".vscode",
    ".cortex",
    "target",
    ".next",
    "*.min.js",
    "*.min.css",
    "*.pyc",
    "*.class",
    "*.o",
    "*.obj",
    "*.exe",
    "*.out",
    "Library",
    "Temp",
    "Logs",
    "obj",
]


def load_gitignore(workspace: str) -> list:
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


def should_include(path: str, workspace: str, settings: dict) -> bool:
    rules = settings.get("indexing_rules", {})
    rel = os.path.relpath(path, workspace)

    def _matches(pattern: str) -> bool:
        if "**" in pattern:
            return Path(rel).match(pattern)
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern)

    whitelist = rules.get("config_whitelist", [])
    for pattern in whitelist:
        if _matches(pattern):
            return True

    includes = rules.get("include_paths", ["**"])
    for pattern in includes:
        if _matches(pattern):
            return True

    modules = rules.get("modules", {})
    if isinstance(modules, dict):
        for _, mod_paths in modules.items():
            for m_path in mod_paths:
                if rel.startswith(m_path) or fnmatch.fnmatch(rel, m_path):
                    return True

    return False


def get_module_name(rel_path: str, settings: dict) -> str:
    norm_path = rel_path.replace("\\", "/")
    modules = settings.get("indexing_rules", {}).get("modules", {})
    if isinstance(modules, dict):
        for mod_name, mod_paths in modules.items():
            for m_path in mod_paths:
                norm_module_path = str(m_path).replace("\\", "/").strip("/")
                if f"{norm_module_path}/" in f"{norm_path}/" or norm_path.endswith(norm_module_path):
                    return mod_name
    parts = norm_path.split("/")
    return parts[0] if len(parts) > 1 else "root"


def scan_files(workspace: str, supported_extensions: dict | None = None, settings_override: dict | None = None) -> list:
    workspace = str(Path(workspace).resolve())
    if settings_override is None:
        binary = ensure_rust_watcher_binary()
        try:
            proc = subprocess.run(
                [str(binary), "scan", "--workspace", workspace, "--format", "json"],
                capture_output=True,
                text=True,
                check=True,
            )
            files = json.loads(proc.stdout) if proc.stdout.strip() else []
        except Exception as e:
            log.warning("Rust scanner failed: %s", e)
            files = []
        return sorted(set(files))

    return _scan_files_python(workspace, supported_extensions or {}, settings_override)


def _scan_files_python(workspace: str, supported_extensions: dict, settings: dict) -> list:
    ignore_patterns = load_gitignore(workspace)

    rules = settings.get("indexing_rules", {})
    extra_excludes = rules.get("exclude_paths", [])
    if extra_excludes:
        ignore_patterns.extend([p.strip("/") for p in extra_excludes if p.strip()])

    files = []

    for index_root in normalize_configured_index_roots(workspace, settings):
        for full_path, db_path in _iter_index_root_files(workspace, index_root, supported_extensions, ignore_patterns):
            if should_include(full_path, workspace, settings):
                files.append(db_path)

    cortex_home = resolve_cortex_home(workspace)
    home_rel = os.path.relpath(str(cortex_home), workspace)
    agent_docs = [
        os.path.join(home_rel, "rules"),
        os.path.join(home_rel, "docs"),
    ]
    for doc_dir in agent_docs:
        abs_doc_dir = os.path.join(workspace, doc_dir)
        if os.path.exists(abs_doc_dir):
            for path in Path(abs_doc_dir).rglob("*.md"):
                files.append(os.path.relpath(str(path), workspace).replace("\\", "/"))

    cortex_scripts_dir = cortex_home / "scripts"
    if cortex_scripts_dir.exists():
        for path in cortex_scripts_dir.rglob("*.py"):
            spath = str(path)
            if any(x in spath for x in ["__pycache__", ".venv", "site-packages"]):
                continue
            files.append(os.path.relpath(spath, workspace).replace("\\", "/"))

    return sorted(list(set(files)))


def _iter_index_root_files(workspace: str, index_root, supported_extensions: dict, ignore_patterns: list):
    root_path = index_root.source_path
    if not root_path.exists():
        return
    if root_path.is_file():
        if root_path.suffix in supported_extensions and not should_ignore(str(root_path), ignore_patterns, workspace):
            yield str(root_path), index_root.db_root
        return
    for root, dirs, filenames in os.walk(root_path):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns, workspace)]
        for fname in filenames:
            full_path = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1]
            if ext in supported_extensions and not should_ignore(full_path, ignore_patterns, workspace):
                rel = os.path.relpath(full_path, str(root_path))
                rel = rel.replace("\\", "/")
                if index_root.db_root == ".":
                    db_path = os.path.relpath(full_path, workspace).replace("\\", "/")
                else:
                    db_path = f"{index_root.db_root}/{rel}"
                yield full_path, db_path
