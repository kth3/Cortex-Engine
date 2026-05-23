from __future__ import annotations

from typing import Any, Mapping

DEFAULT_SNIPPET_MAX_CHARS = 400
DEFAULT_TEXT_SNIPPET_MAX_CHARS = 500

CODE_PRIMARY_FIELDS = (
    "signature",
    "content",
    "code",
    "body",
    "text",
    "raw_body",
)

TEXT_PRIMARY_FIELDS = (
    "snippet",
    "summary",
    "content",
    "observation",
    "body",
    "text",
    "raw_body",
)

LOCATION_FIELDS = (
    "fqn",
    "file_path",
    "path",
    "rel_path",
    "name",
)

CODE_LOCATION_FALLBACK = "→ Capsule 참조 (코드 생략됨)"
GENERIC_EMPTY_SNIPPET = ""


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def normalize_snippet_text(value: Any) -> str:
    text = _as_text(value)
    if not text:
        return ""
    return " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())


def truncate_snippet(text: str, max_chars: int = DEFAULT_SNIPPET_MAX_CHARS) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def first_nonempty_field(row: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        text = normalize_snippet_text(row.get(field))
        if text:
            return text
    return ""


def source_location(row: Mapping[str, Any]) -> str:
    for field in LOCATION_FIELDS:
        text = normalize_snippet_text(row.get(field))
        if text:
            line = row.get("line") or row.get("start_line") or row.get("line_no") or row.get("line_start")
            if line:
                return f"{text}:{line}"
            return text
    return ""


def code_result_snippet(
    row: Mapping[str, Any],
    max_chars: int = DEFAULT_SNIPPET_MAX_CHARS,
) -> str:
    text = first_nonempty_field(row, CODE_PRIMARY_FIELDS)
    if text:
        return truncate_snippet(text, max_chars)

    location = source_location(row)
    if location:
        return f"→ {location} 참조 (코드 본문 생략됨)"

    return CODE_LOCATION_FALLBACK


def text_result_snippet(
    row: Mapping[str, Any],
    max_chars: int = DEFAULT_TEXT_SNIPPET_MAX_CHARS,
) -> str:
    text = first_nonempty_field(row, TEXT_PRIMARY_FIELDS)
    if text:
        return truncate_snippet(text, max_chars)

    location = source_location(row)
    if location:
        return f"→ {location} 참조"

    return GENERIC_EMPTY_SNIPPET


def result_snippet(
    row: Mapping[str, Any],
    domain: str | None = None,
    max_chars: int = DEFAULT_SNIPPET_MAX_CHARS,
) -> str:
    if domain == "code":
        return code_result_snippet(row, max_chars=max_chars)
    return text_result_snippet(row, max_chars=max_chars)
