"""FTS query normalization helpers."""

FTS_PREFIX_SUFFIX = "*"


def escape_fts_phrase(value: str) -> str:
    """SQLite FTS5 phrase query 내부에서 깨질 수 있는 큰따옴표를 이스케이프한다."""
    return value.replace('"', '""')


def normalize_fts_query(query: str) -> str:
    """
    공백 기준으로만 검색어를 분리한다.
    파일 경로, snake_case, camelCase, dotted path는 한 토큰처럼 보존한다.
    각 토큰은 phrase prefix query로 변환한다.
    """
    terms = [term.strip() for term in str(query or "").split() if term.strip()]
    if not terms:
        return ""

    return " OR ".join(f'"{escape_fts_phrase(term)}"{FTS_PREFIX_SUFFIX}' for term in terms)
