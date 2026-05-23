"""Shared constants and helpers for session context assembly."""
from __future__ import annotations

DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET = 2000
AUTO_CONTEXT_DECISION_LIMIT = 5
AUTO_CONTEXT_PATTERN_LIMIT = 3
AUTO_CONTEXT_POPULAR_LIMIT = 5
AUTO_CONTEXT_STANDARD_SNIPPET_CHARS = 150
AUTO_CONTEXT_POPULAR_SNIPPET_CHARS = 100

AUTO_CONTEXT_DECISION_CATEGORY = "decision"
AUTO_CONTEXT_PATTERN_CATEGORY = "pattern"

SQL_RECENT_DECISIONS = (
    "SELECT key, content, updated_at FROM memories "
    f"WHERE category = '{AUTO_CONTEXT_DECISION_CATEGORY}' "
    f"ORDER BY updated_at DESC LIMIT {AUTO_CONTEXT_DECISION_LIMIT}"
)
SQL_RECENT_PATTERNS = (
    "SELECT key, content, updated_at FROM memories "
    f"WHERE category = '{AUTO_CONTEXT_PATTERN_CATEGORY}' "
    f"ORDER BY updated_at DESC LIMIT {AUTO_CONTEXT_PATTERN_LIMIT}"
)
SQL_POPULAR_MEMORIES = (
    "SELECT key, category, content, access_count FROM memories "
    "WHERE access_count > 0 "
    f"ORDER BY access_count DESC LIMIT {AUTO_CONTEXT_POPULAR_LIMIT}"
)

STATE_DIRNAME = "state"
BOARD_JSON_FILE = "board.json"
BOARD_LANES_KEY = "lanes"
CONTRACT_ID_KEY = "contract_id"

TEXT_FILE_ENCODING = "utf-8"


def _fetch_rows(conn, sql):
    return conn.execute(sql).fetchall()


def _append_entry_with_budget(sections, total_chars, entry, token_budget):
    if total_chars + len(entry) > token_budget:
        return total_chars, False
    sections.append(entry)
    return total_chars + len(entry), True
