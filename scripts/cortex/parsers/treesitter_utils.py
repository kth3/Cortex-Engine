"""
Tree-sitter 파서 공통 유틸리티
모든 treesitter_*_parser.py에서 import하여 사용.
"""
import re
import uuid
from tree_sitter import Language, Parser

# ── Language 로드 (0.22+ Pre-compiled Wheel API) ────────────────
try:
    import tree_sitter_c_sharp as _ts_cs
    CS_LANGUAGE = Language(_ts_cs.language())
except ImportError:
    CS_LANGUAGE = None

try:
    import tree_sitter_typescript as _ts_ts
    TS_LANGUAGE = Language(_ts_ts.language_typescript())
    TSX_LANGUAGE = Language(_ts_ts.language_tsx())
except ImportError:
    TS_LANGUAGE = None
    TSX_LANGUAGE = None


def txt(node) -> str:
    """노드 텍스트를 UTF-8 문자열로 반환"""
    return node.text.decode("utf-8") if node else ""


def name_of(node) -> str:
    """노드의 name 필드 텍스트 반환"""
    n = node.child_by_field_name("name")
    return txt(n) if n else ""


def truncate(text: str, mx: int) -> str:
    return text[:mx] if text and len(text) > mx else (text or "")


def make_id(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def build_fqn(node, file_path: str) -> str:
    """node.parent 역추적으로 namespace::class::name FQN 조립"""
    parts = []
    cur = node
    while cur is not None:
        if cur.type in (
            "class_declaration", "interface_declaration", "struct_declaration",
            "enum_declaration", "namespace_declaration", "record_declaration",
            "method_declaration", "constructor_declaration",
        ):
            name = name_of(cur)
            if name:
                parts.insert(0, name)
        cur = cur.parent
    return f"{file_path}::{'::'.join(parts)}" if parts else file_path


def extract_type_names(text: str) -> list[str]:
    """텍스트에서 대문자로 시작하는 타입명 추출"""
    return [m.group(1) for m in re.finditer(r'([A-Z][A-Za-z0-9_]*)', text)]
