"""Shared MCP registry helpers and constants."""
from __future__ import annotations


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

