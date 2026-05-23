import json
import subprocess
import tempfile
from pathlib import Path

from cortex.logger import get_logger
from cortex.runtime.paths import ensure_rust_watcher_binary

log = get_logger("parser_registry")


def _parse_with_rust(fp, src, abs_path=None):
    tmp_path = None
    try:
        binary = ensure_rust_watcher_binary()
        ext = Path(fp).suffix.lower()

        if ext == ".pdf":
            file_path = Path(abs_path) if abs_path else Path(fp)
            command_path = file_path
        else:
            with tempfile.NamedTemporaryFile(
                "w", suffix=ext or ".txt", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(src)
                tmp_path = Path(tmp.name)
            command_path = tmp_path

        proc = subprocess.run(
            [str(binary), "parse-file", "--rel", fp, str(command_path)],
            capture_output=True,
            text=True,
            check=True,
            cwd=None,
        )
        return json.loads(proc.stdout)
    except Exception as e:
        log.warning("Failed to parse file via Rust parser: %s", e)
        return {"nodes": [], "edges": []}
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


class ParserRegistry:
    def __init__(self):
        self.parsers = {}
        self._load_parsers()

    def _load_parsers(self):
        rust_parsers = {
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".java": "java",
            ".md": "markdown",
            ".html": "html",
            ".css": "css",
            ".pdf": "pdf",
            ".py": "python",
            ".cs": "csharp",
            ".ts": "typescript",
            ".tsx": "typescript",
        }

        for ext, language in rust_parsers.items():
            self.parsers[ext] = (language, lambda fp, src, abs_path=None: _parse_with_rust(fp, src, abs_path))

    def get_parser(self, ext: str):
        return self.parsers.get(ext, (None, None))

    def get_supported_extensions(self):
        return list(self.parsers.keys())


registry = ParserRegistry()
parser_registry = registry
SUPPORTED_EXTENSIONS = registry.parsers
get_parser = registry.get_parser
