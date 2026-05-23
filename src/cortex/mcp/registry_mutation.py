"""Mutation-oriented MCP tool definitions."""
from __future__ import annotations

from .registry_common import (
    DEFAULT_MEMORY_CONSOLIDATE_DRY_RUN,
    DEFAULT_SESSION_CONTEXT_TOKEN_BUDGET,
    TODO_ACTIONS,
    TOOL_CONSOLIDATE_MEMORY,
    TOOL_CREATE_TASK_CONTRACT,
    TOOL_MANAGE_TODO,
    TOOL_READ_MEMORY,
    TOOL_REPLACE_EXACT_TEXT,
    TOOL_SAVE_OBSERVATION,
    TOOL_SEARCH_MEMORY,
    TOOL_GET_SESSION_CONTEXT,
    TOOL_SYNC_SESSION_MEMORY,
    TOOL_WRITE_MEMORY,
    _array_string_property,
    _boolean_property,
    _integer_property,
    _object_property,
    _string_property,
    _tool,
)


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


MUTATION_TOOLS = [
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
