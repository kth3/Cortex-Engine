import sys
from cortex.logger import get_logger

log = get_logger("parser_registry")

class ParserRegistry:
    def __init__(self):
        self.parsers = {}
        # 초기화 시점에 파서 로드
        self._load_parsers()

    def _load_parsers(self):
        # 1. C/C++
        try:
            from cortex.parsers.c_parser import parse_c_file
            self.parsers[".c"] = ("c", lambda fp, src: parse_c_file(fp, src))
            self.parsers[".cpp"] = ("cpp", lambda fp, src: parse_c_file(fp, src))
            self.parsers[".h"] = ("c", lambda fp, src: parse_c_file(fp, src))
            self.parsers[".hpp"] = ("cpp", lambda fp, src: parse_c_file(fp, src))
        except Exception as e:
            log.warning("Failed to load C/C++ parser: %s", e)

        # 2. Java
        try:
            from cortex.parsers.java_parser import parse_java_file
            self.parsers[".java"] = ("java", lambda fp, src: parse_java_file(fp, src))
        except Exception as e:
            log.warning("Failed to load Java parser: %s", e)

        # 3. Markdown / HTML / CSS
        try:
            from cortex.parsers.markdown_parser import parse_markdown_file
            self.parsers[".md"] = ("markdown", lambda fp, src: parse_markdown_file(fp, src))
            self.parsers[".html"] = ("html", lambda fp, src: parse_markdown_file(fp, src))
            self.parsers[".css"] = ("css", lambda fp, src: parse_markdown_file(fp, src))
        except Exception as e:
            log.warning("Failed to load Markdown parser: %s", e)

        # 4. PDF
        try:
            from cortex.parsers.pdf_parser import parse_pdf_file
            self.parsers[".pdf"] = ("pdf", lambda fp, _: parse_pdf_file(fp))
        except Exception as e:
            log.warning("Failed to load PDF parser: %s", e)

        # 5. Python
        try:
            from cortex.parsers.python_parser import parse_python_file
            self.parsers[".py"] = ("python", lambda fp, src: parse_python_file(fp, src))
        except Exception as e:
            log.warning("Failed to load Python parser: %s", e)

        # 6. Tree-sitter C#
        try:
            from cortex.parsers.treesitter_utils import CS_LANGUAGE
            if CS_LANGUAGE is not None:
                from cortex.parsers.treesitter_cs_parser import parse_csharp_file
                self.parsers[".cs"] = ("csharp", lambda fp, src: parse_csharp_file(fp, src))
        except Exception as e:
            log.warning("Failed to load C# Tree-sitter parser: %s", e)

        # 7. Tree-sitter TypeScript
        try:
            from cortex.parsers.treesitter_utils import TS_LANGUAGE, TSX_LANGUAGE
            if TS_LANGUAGE is not None:
                from cortex.parsers.treesitter_ts_parser import parse_ts_file
                self.parsers[".ts"] = ("typescript", lambda fp, src: parse_ts_file(fp, src, "typescript"))
            if TSX_LANGUAGE is not None:
                from cortex.parsers.treesitter_ts_parser import parse_ts_file
                self.parsers[".tsx"] = ("typescript", lambda fp, src: parse_ts_file(fp, src, "tsx"))
        except Exception as e:
            log.warning("Failed to load TypeScript Tree-sitter parser: %s", e)

    def get_parser(self, ext: str):
        """확장자에 해당하는 (language, parser_func) 반환. 없으면 (None, None)"""
        return self.parsers.get(ext, (None, None))

    def get_supported_extensions(self):
        """지원하는 모든 확장자 목록 반환"""
        return list(self.parsers.keys())

# 싱글톤 인스턴스로 제공
registry = ParserRegistry()
parser_registry = registry
SUPPORTED_EXTENSIONS = registry.parsers
get_parser = registry.get_parser
