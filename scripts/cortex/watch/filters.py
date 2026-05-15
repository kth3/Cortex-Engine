import fnmatch


ALLOWED_EXTENSIONS = [
    ".py",
    ".md",
    ".txt",
    ".js",
    ".ts",
    ".json",
    ".pdf",
    ".cs",
    ".asset",
    ".prefab",
    ".meta",
    ".inputsettings",
]

BLACKLIST_SEGMENTS = [
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    ".cortex/data/",
    ".cortex/history/",
    ".cortex/artifacts/",
    "/.plastic/",
    "\\.plastic\\",
    "/Library/",
    "\\Library\\",
    "/Temp/",
    "\\Temp\\",
    "/Logs/",
    "\\Logs\\",
    "/obj/",
    "\\obj\\",
    "/UserSettings/",
    "\\UserSettings\\",
    "/Builds/",
    "\\Builds\\",
    "/MemoryCaptures/",
    "\\MemoryCaptures\\",
    "/.vs/",
    "\\.vs\\",
    "/.idea/",
    "\\.idea\\",
    "/.vscode/",
    "\\.vscode\\",
    "/dist/",
    "\\dist\\",
    "/build/",
    "\\build\\",
]

CORTEX_ALLOWED_SEGMENTS = [
    "/rules/",
    "/knowledge/",
    "/skills/",
    "/docs/",
    "/scripts/",
]


def normalize_patterns(patterns):
    return [p.replace("\\", "/").strip("/") for p in patterns if p.strip()]


def is_valid_file(path_str, exclude_paths=None):
    path_str = path_str.replace("\\", "/")
    exclude_paths = exclude_paths or []

    for pattern in exclude_paths:
        if fnmatch.fnmatch(path_str, pattern):
            return False

    if any(x in path_str for x in BLACKLIST_SEGMENTS):
        return False

    if ".cortex/" in path_str:
        return any(x in path_str for x in CORTEX_ALLOWED_SEGMENTS)

    return any(path_str.endswith(ext) for ext in ALLOWED_EXTENSIONS)
