def _string_property(description=None, enum=None, default=None):
    prop = {"type": "string"}
    if description is not None:
        prop["description"] = description
    if enum is not None:
        prop["enum"] = list(enum)
    if default is not None:
        prop["default"] = default
    return prop


def _integer_property(description=None, default=None):
    prop = {"type": "integer"}
    if description is not None:
        prop["description"] = description
    if default is not None:
        prop["default"] = default
    return prop


def _boolean_property(description=None, default=None):
    prop = {"type": "boolean"}
    if description is not None:
        prop["description"] = description
    if default is not None:
        prop["default"] = default
    return prop


def _array_string_property(description=None):
    prop = {"type": "array", "items": {"type": "string"}}
    if description is not None:
        prop["description"] = description
    return prop


def _object_property(description=None):
    prop = {"type": "object"}
    if description is not None:
        prop["description"] = description
    return prop


def _input_schema(properties=None, required=None):
    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = list(required)
    return schema


def _tool(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": _input_schema(properties, required),
    }


# ── Tool name constants ────────────────────────────────────────────
TOOL_GET_INDEX_STATUS = "get_index_status"
TOOL_SEARCH_CONTEXT = "search_context"
TOOL_SEARCH_DEEP_CONTEXT = "search_deep_context"
TOOL_GET_FILE_OUTLINE = "get_file_outline"
TOOL_READ_FILE_WITH_HASH = "read_file_with_hash"
TOOL_RESOLVE_SYMBOL = "resolve_symbol"
TOOL_GET_IMPACT_GRAPH = "get_impact_graph"
TOOL_FIND_EXECUTION_PATH = "find_execution_path"
TOOL_GET_FILE_GIT_HISTORY = "get_file_git_history"
TOOL_REPLACE_EXACT_TEXT = "replace_exact_text"
TOOL_GET_SESSION_CONTEXT = "get_session_context"
TOOL_SYNC_SESSION_MEMORY = "sync_session_memory"
TOOL_WRITE_MEMORY = "write_memory"
TOOL_CONSOLIDATE_MEMORY = "consolidate_memory"
TOOL_READ_MEMORY = "read_memory"
TOOL_SAVE_OBSERVATION = "save_observation"
TOOL_SEARCH_MEMORY = "search_memory"
TOOL_CREATE_TASK_CONTRACT = "create_task_contract"
TOOL_MANAGE_TODO = "manage_todo"

# ── Defaults ───────────────────────────────────────────────────────
DEFAULT_SEARCH_CONTEXT_TOKEN_BUDGET = 4000
DEFAULT_FILE_OUTLINE_DETAIL = "standard"
FILE_OUTLINE_DETAIL_LEVELS = ("minimal", "standard", "detailed")
DEFAULT_IMPACT_DIRECTION = "both"
IMPACT_DIRECTIONS = ("callers", "callees", "both")
DEFAULT_IMPACT_MAX_DEPTH = 2
DEFAULT_IMPACT_MAX_NODES = 50
DEFAULT_LOGIC_MAX_DEPTH = 6
DEFAULT_LOGIC_MAX_NODES = 200
DEFAULT_GIT_HISTORY_LIMIT = 5
DEFAULT_DEEP_CONTEXT_LIMIT = 5
DEFAULT_SESSION_CONTEXT_TOKEN_BUDGET = 2000
DEFAULT_RESOLVE_SYMBOL_LIMIT = 5
DEFAULT_MEMORY_CONSOLIDATE_DRY_RUN = True
TODO_ACTIONS = ("add", "check", "clear")


# ── Read-only / Context ────────────────────────────────────────────

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


# ── Symbol / Graph ─────────────────────────────────────────────────

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


# ── Edit / Write ───────────────────────────────────────────────────

def _replace_exact_text_tool():
    return _tool(
        TOOL_REPLACE_EXACT_TEXT,
        "Replace an exact text fragment in a file. "
        "Always call read_file_with_hash first to obtain the exact current file content. "
        "This is a write operation with side effects. "
        "Fails safely if old_content does not match the current file content exactly. "
        "Triggers after-edit hooks and records an edit event in the Cortex database.",
        {
            "file_path": _string_property("Workspace-relative path to the file to edit."),
            "old_content": _string_property(
                "Exact text to replace. Must match the current file content character-for-character."
            ),
            "new_content": _string_property("Replacement text."),
        },
        ["file_path", "old_content", "new_content"],
    )


# ── Session / Memory ───────────────────────────────────────────────

def _get_session_context_tool():
    return _tool(
        TOOL_GET_SESSION_CONTEXT,
        "Return a summary of recent decisions, patterns, and frequently-accessed knowledge to restore session context. "
        "Call at the start of a session when prior work context is needed. Read-only.",
        {
            "token_budget": _integer_property(
                "Maximum response size in approximate tokens (chars/4 estimate). Default 2000.",
                DEFAULT_SESSION_CONTEXT_TOKEN_BUDGET,
            ),
        },
    )


def _sync_session_memory_tool():
    return _tool(
        TOOL_SYNC_SESSION_MEMORY,
        "Synchronize session state to persistent memory by scanning git status and recently modified files. "
        "Call at the end of a meaningful work session (code edits, design decisions, completed exploration). "
        "Side-effect: writes a session-sync memory record and updates memory.yaml. "
        "Not calling this will cause incomplete context restoration in the next session.",
        {
            "task_desc": _string_property("Brief description of work completed in this session."),
        },
        ["task_desc"],
    )


def _write_memory_tool():
    return _tool(
        TOOL_WRITE_MEMORY,
        "Write a keyed knowledge record to persistent memory. "
        "Side-effect: persists to the memory database and optionally promotes to markdown history files "
        "(decisions.md for 'decision'/'architecture' categories; patterns.md for 'pattern'/'convention'/'rule'/'protocol').",
        {
            "key": _string_property("Unique identifier for this memory record."),
            "category": _string_property(
                "Semantic category (e.g. 'decision', 'architecture', 'pattern', 'convention', 'rule', 'insight')."
            ),
            "content": _string_property("The knowledge content to store."),
            "tags": _array_string_property("Optional list of searchable tags."),
            "relationships": _object_property("Optional relationship map (e.g. {'related_to': ['key1']})."),
        },
        ["key", "category", "content"],
    )


def _consolidate_memory_tool():
    return _tool(
        TOOL_CONSOLIDATE_MEMORY,
        "Merge multiple fragmented memory records into a single consolidated record. "
        "Side-effect when dry_run=false: deletes old_keys and writes the new consolidated record. "
        "dry_run=true (default) returns a preview of what would be deleted and written without making changes. "
        "Do not trigger automatically; only use when explicitly requested.",
        {
            "new_key": _string_property("Key for the consolidated memory record."),
            "category": _string_property("Category for the consolidated record."),
            "content": _string_property("Merged content for the consolidated record."),
            "old_keys": _array_string_property("List of existing memory keys to delete after consolidation."),
            "tags": _array_string_property("Optional tags for the consolidated record."),
            "relationships": _object_property("Optional relationship map for the consolidated record."),
            "dry_run": _boolean_property(
                "If true (default), return a preview without making any changes. "
                "Set to false to perform actual deletion and write.",
                DEFAULT_MEMORY_CONSOLIDATE_DRY_RUN,
            ),
        },
        ["new_key", "category", "content", "old_keys"],
    )


def _read_memory_tool():
    return _tool(
        TOOL_READ_MEMORY,
        "Read a single memory record by its exact key. Read-only. "
        "Use search_memory to find records when the exact key is unknown.",
        {
            "key": _string_property("Exact key of the memory record to retrieve."),
        },
        ["key"],
    )


def _save_observation_tool():
    return _tool(
        TOOL_SAVE_OBSERVATION,
        "Record a short observation or insight about code, decisions, or discoveries made during this session. "
        "Side-effect: writes to the observation log and triggers after-save hooks. "
        "Use after meaningful code edits, bug discoveries, or design decisions.",
        {
            "content": _string_property("The observation content to record."),
        },
        ["content"],
    )


def _search_memory_tool():
    return _tool(
        TOOL_SEARCH_MEMORY,
        "Hybrid search over persistent knowledge, rules, and skills. "
        "Use to look up stored decisions, patterns, architecture notes, or project conventions. Read-only. "
        "Filter by category to narrow results (e.g. category='skill' or category='rule').",
        {
            "query": _string_property("Natural-language or keyword query."),
            "category": _string_property(
                "Optional: filter by category (e.g. 'skill', 'rule', 'decision', 'architecture', 'insight')."
            ),
        },
        ["query"],
    )


# ── Orchestration ──────────────────────────────────────────────────

def _create_task_contract_tool():
    return _tool(
        TOOL_CREATE_TASK_CONTRACT,
        "Create a task contract specifying the work scope, instructions, and files to be modified. "
        "Use before starting any task that involves 3 or more file changes or architectural decisions. "
        "Side-effect: writes contract to the board state and records an observation.",
        {
            "lane_id": _string_property("Lane identifier for multi-agent coordination."),
            "task_name": _string_property("Short name for this task."),
            "instructions": _string_property("Full task description and implementation instructions."),
            "files_to_modify": _array_string_property(
                "Optional list of workspace-relative file paths that will be modified."
            ),
        },
        ["lane_id", "task_name", "instructions"],
    )


def _manage_todo_tool():
    return _tool(
        TOOL_MANAGE_TODO,
        "Add, check off, or clear items in the session todo list. "
        "Side-effect: modifies the todo list state. "
        "Use add to register a new task, check to mark it complete (requires task_id), clear to reset the list.",
        {
            "action": _string_property(
                "Action to perform: 'add' a new task, 'check' a task as done, or 'clear' the entire list.",
                enum=TODO_ACTIONS,
            ),
            "task": _string_property("Task description. Required when action='add'."),
            "task_id": _string_property("Task ID to mark complete. Required when action='check'."),
        },
        ["action"],
    )


TOOLS = [
    _get_index_status_tool(),
    _search_context_tool(),
    _search_deep_context_tool(),
    _get_file_outline_tool(),
    _read_file_with_hash_tool(),
    _resolve_symbol_tool(),
    _get_impact_graph_tool(),
    _find_execution_path_tool(),
    _get_file_git_history_tool(),
    _replace_exact_text_tool(),
    _get_session_context_tool(),
    _sync_session_memory_tool(),
    _write_memory_tool(),
    _consolidate_memory_tool(),
    _read_memory_tool(),
    _save_observation_tool(),
    _search_memory_tool(),
    _create_task_contract_tool(),
    _manage_todo_tool(),
]


def list_tools():
    return TOOLS
