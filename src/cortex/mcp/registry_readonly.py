"""Read-only MCP tool definitions."""
from __future__ import annotations

from .registry_common import (
    DEFAULT_DEEP_CONTEXT_LIMIT,
    DEFAULT_FILE_OUTLINE_DETAIL,
    DEFAULT_GIT_HISTORY_LIMIT,
    DEFAULT_IMPACT_DIRECTION,
    DEFAULT_IMPACT_MAX_DEPTH,
    DEFAULT_IMPACT_MAX_NODES,
    DEFAULT_LOGIC_MAX_DEPTH,
    DEFAULT_LOGIC_MAX_NODES,
    DEFAULT_RESOLVE_SYMBOL_LIMIT,
    DEFAULT_SEARCH_CONTEXT_TOKEN_BUDGET,
    FILE_OUTLINE_DETAIL_LEVELS,
    IMPACT_DIRECTIONS,
    TOOL_FIND_EXECUTION_PATH,
    TOOL_GET_FILE_GIT_HISTORY,
    TOOL_GET_FILE_OUTLINE,
    TOOL_GET_IMPACT_GRAPH,
    TOOL_GET_INDEX_STATUS,
    TOOL_READ_FILE_WITH_HASH,
    TOOL_RESOLVE_SYMBOL,
    TOOL_SEARCH_CONTEXT,
    TOOL_SEARCH_DEEP_CONTEXT,
    _integer_property,
    _string_property,
    _tool,
)


def _get_index_status_tool():
    return _tool(
        TOOL_GET_INDEX_STATUS,
        "Return Cortex index and database status: node/edge/file/memory counts and schema version. "
        "Use to verify the index is populated before running graph or search tools. Read-only.",
    )


def _search_context_tool():
    return _tool(
        TOOL_SEARCH_CONTEXT,
        "Search compact project context across code, documentation, and stored knowledge. "
        "Use this first when answering codebase questions, looking up implementations, or tracing design history. "
        "Returns a compact capsule with estimated token usage. "
        "Read-only. No side effects. "
        "Do not use for exact file editing; call read_file_with_hash before replace_exact_text.",
        {
            "query": _string_property("Natural-language or keyword query describing what you are looking for."),
            "token_budget": _integer_property(
                "Maximum response size in approximate tokens (chars/4 estimate). Default 4000.",
                DEFAULT_SEARCH_CONTEXT_TOKEN_BUDGET,
            ),
        },
        ["query"],
    )


def _search_deep_context_tool():
    return _tool(
        TOOL_SEARCH_DEEP_CONTEXT,
        "Run a comprehensive search combining code index, call graph, and memory for complex questions. "
        "Use when search_context returns insufficient context, or when the question requires cross-cutting "
        "code + architecture + decision history. "
        "When the code capsule result is sparse, automatically chains in related memory entries for broader context. "
        "Response includes capsule_chars for gauging result density; chained_memories is present when chaining triggered. "
        "Slower than search_context. Read-only. No side effects. "
        "Do not use for simple keyword lookups; prefer search_context for those.",
        {
            "query": _string_property("Natural-language query for comprehensive cross-domain search."),
            "limit": _integer_property(
                "Maximum number of unified result items to return. Default 5.",
                DEFAULT_DEEP_CONTEXT_LIMIT,
            ),
        },
        ["query"],
    )


def _get_file_outline_tool():
    return _tool(
        TOOL_GET_FILE_OUTLINE,
        "Return the structural outline of a file: classes, functions, methods, and key symbols — "
        "without reading the full content. "
        "Use before reading large files to decide which sections need full inspection. Read-only.",
        {
            "file_path": _string_property("Workspace-relative path to the file."),
            "detail": _string_property(
                "Outline verbosity: 'minimal' (names only), 'standard' (signatures), 'detailed' (includes docstrings). Default 'standard'.",
                enum=FILE_OUTLINE_DETAIL_LEVELS,
                default=DEFAULT_FILE_OUTLINE_DETAIL,
            ),
        },
        ["file_path"],
    )


def _read_file_with_hash_tool():
    return _tool(
        TOOL_READ_FILE_WITH_HASH,
        "Read the current content of a file and return its content hash. "
        "Always call this before replace_exact_text to obtain the exact current text. Read-only. "
        "The returned hash is used internally to detect concurrent modifications.",
        {
            "file_path": _string_property("Workspace-relative path to the file to read."),
        },
        ["file_path"],
    )


def _resolve_symbol_tool():
    return _tool(
        TOOL_RESOLVE_SYMBOL,
        "Resolve a class, function, method, or partial symbol name into fully-qualified name (FQN) candidates. "
        "Uses three-stage lookup: exact FQN match → FTS keyword search → vector similarity search (when embeddings are available). "
        "Use before get_impact_graph or find_execution_path when the exact FQN is unknown. Read-only. "
        "Returns a list of candidates with fqn, kind, language, file_path, line, and match_reason (exact_fqn | fts_match | vector_match). "
        "If no matches are found, returns an empty list with a next_suggestion.",
        {
            "name": _string_property(
                "Symbol name to resolve. May be a short name, partial path, or exact FQN."
            ),
            "file_path": _string_property(
                "Optional: narrow results to symbols defined in this file (workspace-relative)."
            ),
            "language": _string_property(
                "Optional: narrow results to a specific language (e.g. 'python', 'typescript')."
            ),
            "limit": _integer_property(
                f"Maximum number of FQN candidates to return. Default {DEFAULT_RESOLVE_SYMBOL_LIMIT}.",
                DEFAULT_RESOLVE_SYMBOL_LIMIT,
            ),
        },
        ["name"],
    )


def _get_impact_graph_tool():
    return _tool(
        TOOL_GET_IMPACT_GRAPH,
        "Return callers, callees, or both for a given fully-qualified name (FQN) up to a specified depth. "
        "Use to understand the blast radius of a change or to trace who uses a symbol. "
        "If you do not know the exact FQN, call resolve_symbol first. Read-only. "
        "Response includes truncated, limit, returned_count, total_seen metadata.",
        {
            "fqn": _string_property(
                "Exact fully-qualified name of the symbol. Use resolve_symbol first if unknown."
            ),
            "direction": _string_property(
                "Which edges to traverse: 'callers' (who calls this), 'callees' (what this calls), or 'both'. Default 'both'.",
                enum=IMPACT_DIRECTIONS,
                default=DEFAULT_IMPACT_DIRECTION,
            ),
            "max_depth": _integer_property(
                "Maximum traversal depth from the root symbol. Default 2.",
                DEFAULT_IMPACT_MAX_DEPTH,
            ),
            "max_nodes": _integer_property(
                "Maximum number of nodes to return. Default 50.",
                DEFAULT_IMPACT_MAX_NODES,
            ),
        },
        ["fqn"],
    )


def _find_execution_path_tool():
    return _tool(
        TOOL_FIND_EXECUTION_PATH,
        "Find the call path between two symbols identified by their fully-qualified names (FQN). "
        "Use to understand how execution flows from one function to another. "
        "If you do not know the exact FQNs, call resolve_symbol first for each symbol. Read-only. "
        "Response includes path (list of FQNs), truncated, limit, returned_count, total_seen metadata.",
        {
            "from_fqn": _string_property(
                "FQN of the starting symbol. Use resolve_symbol first if unknown."
            ),
            "to_fqn": _string_property(
                "FQN of the ending symbol. Use resolve_symbol first if unknown."
            ),
            "max_depth": _integer_property(
                "Maximum path length in hops. Default 6.",
                DEFAULT_LOGIC_MAX_DEPTH,
            ),
            "max_nodes": _integer_property(
                "Maximum nodes to explore during BFS. Default 200.",
                DEFAULT_LOGIC_MAX_NODES,
            ),
        },
        ["from_fqn", "to_fqn"],
    )


def _get_file_git_history_tool():
    return _tool(
        TOOL_GET_FILE_GIT_HISTORY,
        "Return the git commit history for a specific file. "
        "Use to understand when and why a file was changed. Read-only.",
        {
            "file_path": _string_property("Workspace-relative path to the file."),
            "limit": _integer_property(
                "Maximum number of commits to return. Default 5.",
                DEFAULT_GIT_HISTORY_LIMIT,
            ),
        },
        ["file_path"],
    )


READONLY_TOOLS = [
    _get_index_status_tool(),
    _search_context_tool(),
    _search_deep_context_tool(),
    _get_file_outline_tool(),
    _read_file_with_hash_tool(),
    _resolve_symbol_tool(),
    _get_impact_graph_tool(),
    _find_execution_path_tool(),
    _get_file_git_history_tool(),
]

