"""Session context MCP tool handlers."""
from __future__ import annotations

import json

from cortex import paths as pc_paths
from cortex import storage as pc_db
from cortex.retrieval.snippets import text_result_snippet

from .session_sync import (
    AUTO_CONTEXT_DECISION_CATEGORY,
    AUTO_CONTEXT_PATTERN_CATEGORY,
    AUTO_CONTEXT_POPULAR_SNIPPET_CHARS,
    AUTO_CONTEXT_STANDARD_SNIPPET_CHARS,
    DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET,
    BOARD_JSON_FILE,
    BOARD_LANES_KEY,
    CONTRACT_ID_KEY,
    SQL_POPULAR_MEMORIES,
    SQL_RECENT_DECISIONS,
    SQL_RECENT_PATTERNS,
    STATE_DIRNAME,
    TEXT_FILE_ENCODING,
    _append_entry_with_budget,
    _fetch_rows,
)


def _recent_memory_entry(row, category, snippet_chars=AUTO_CONTEXT_STANDARD_SNIPPET_CHARS):
    data = dict(row)
    snippet = text_result_snippet(data, max_chars=snippet_chars)
    return f"[{category}] {data['key']}: {snippet}"


def _popular_memory_entry(row):
    data = dict(row)
    snippet = text_result_snippet(data, max_chars=AUTO_CONTEXT_POPULAR_SNIPPET_CHARS)
    return (
        f"[{data['category']}] {data['key']} (hits:{data['access_count']}): {snippet}",
        data["key"],
    )


def _append_recent_memory_sections(conn, sections, total_chars, token_budget, sql, category):
    rows = _fetch_rows(conn, sql)
    for row in rows:
        entry = _recent_memory_entry(row, category)
        total_chars, added = _append_entry_with_budget(sections, total_chars, entry, token_budget)
        if not added:
            break
    return total_chars


def _append_popular_memory_sections(conn, sections, total_chars, token_budget):
    rows = _fetch_rows(conn, SQL_POPULAR_MEMORIES)
    for row in rows:
        entry, key = _popular_memory_entry(row)
        if total_chars + len(entry) > token_budget:
            break
        if not any(key in section for section in sections):
            sections.append(entry)
            total_chars += len(entry)
    return total_chars


def _board_path(ctx):
    return pc_paths.data_dir(ctx.workspace) / STATE_DIRNAME / BOARD_JSON_FILE


def _append_contract_context(ctx, sections, total_chars):
    board_path = _board_path(ctx)
    if board_path.exists():
        try:
            board = json.loads(board_path.read_text(encoding=TEXT_FILE_ENCODING))
            for lane_id, lane in board.get(BOARD_LANES_KEY, {}).items():
                if lane.get(CONTRACT_ID_KEY):
                    entry = f"[contract] lane={lane_id}: {lane[CONTRACT_ID_KEY]}"
                    sections.append(entry)
                    total_chars += len(entry)
        except Exception:
            pass
    return total_chars


def call_get_session_context(ctx, args):
    token_budget = args.get("token_budget", DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET)
    conn = pc_db.get_connection(ctx.workspace)
    try:
        sections = []
        total_chars = 0

        total_chars = _append_recent_memory_sections(
            conn,
            sections,
            total_chars,
            token_budget,
            SQL_RECENT_DECISIONS,
            AUTO_CONTEXT_DECISION_CATEGORY,
        )
        total_chars = _append_recent_memory_sections(
            conn,
            sections,
            total_chars,
            token_budget,
            SQL_RECENT_PATTERNS,
            AUTO_CONTEXT_PATTERN_CATEGORY,
        )
        total_chars = _append_popular_memory_sections(conn, sections, total_chars, token_budget)
        total_chars = _append_contract_context(ctx, sections, total_chars)

        return {
            "context": "\n".join(sections),
            "totalChars": total_chars,
            "itemCount": len(sections),
        }
    finally:
        conn.close()

