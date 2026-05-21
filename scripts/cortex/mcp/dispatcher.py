"""
Cortex MCP Tool Dispatcher

- 책임: 클라이언트(LLM)가 보낸 MCP Tool 호출 요청을 파싱하여, 적절한 내부 도메인 함수로 라우팅한다.
- 주의: 이 모듈은 MCP tool routing과 response format 생성의 계약을 엄격히 지켜야 하며, 도메인 로직을 직접 구현하지 않는다.
"""
import json
from cortex.hooks import manager as pc_hooks
from cortex.mcp.response import create_text_response, create_error_response

from cortex.mcp.tools.indexing import call_get_index_status
from cortex.mcp.tools.search import (
    call_search_context, call_get_file_outline, call_get_impact_graph,
    call_find_execution_path, call_search_deep_context
)
from cortex.mcp.tools.symbols import call_resolve_symbol
from cortex.mcp.tools.edit import (
    call_read_file_with_hash, call_replace_exact_text
)
from cortex.mcp.tools.git import call_get_file_git_history
from cortex.mcp.tools.memory import (
    call_save_observation, call_write_memory, call_consolidate_memory,
    call_read_memory, call_search_memory
)
from cortex.mcp.tools.session import (
    call_get_session_context, call_sync_session_memory
)
from cortex.mcp.tools.orchestration import (
    call_todo_manager, call_create_contract
)

# Guard hook은 write/side-effect/orchestration tool에만 적용한다.
# read-only search tool은 guard 대상에서 제외한다.
GUARDED_TOOL_NAMES = frozenset(
    {
        "replace_exact_text",
        "create_task_contract",
        "manage_todo",
        "sync_session_memory",
        "save_observation",
        "write_memory",
        "consolidate_memory",
    }
)

# Tool 이름과 내부 handler의 매핑.
TOOL_HANDLERS = {
    "get_index_status": call_get_index_status,
    "search_context": call_search_context,
    "search_deep_context": call_search_deep_context,
    "get_file_outline": call_get_file_outline,
    "read_file_with_hash": call_read_file_with_hash,
    "resolve_symbol": call_resolve_symbol,
    "get_impact_graph": call_get_impact_graph,
    "find_execution_path": call_find_execution_path,
    "get_file_git_history": call_get_file_git_history,
    "replace_exact_text": call_replace_exact_text,
    "get_session_context": call_get_session_context,
    "sync_session_memory": call_sync_session_memory,
    "write_memory": call_write_memory,
    "consolidate_memory": call_consolidate_memory,
    "read_memory": call_read_memory,
    "save_observation": call_save_observation,
    "search_memory": call_search_memory,
    "create_task_contract": call_create_contract,
    "manage_todo": call_todo_manager,
}


def _guard_blocked_response(request_id, guard_res: str):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": f"Guard Blocked: {guard_res}",
                }
            ],
        },
    }


def _unknown_tool_response(request_id, tool_name):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"Unknown tool: {tool_name}",
        },
    }


def _run_before_tool_hook(ctx, tool_name: str, arguments: dict, request_id):
    if tool_name not in GUARDED_TOOL_NAMES:
        return None, ""

    guard_res = pc_hooks.dispatch(
        ctx.workspace,
        "before_tool_call",
        tool_name,
        json.dumps(arguments),
    )

    if guard_res and isinstance(guard_res, str):
        if guard_res.startswith("Error:"):
            return _guard_blocked_response(request_id, guard_res), ""
        if guard_res.startswith("Info:"):
            return None, f"[{guard_res}]\n"
        return None, f"[Hook: {guard_res}]\n"

    return None, ""


def handle_tools_call(ctx, params, request_id):
    n, a = params.get("name"), params.get("arguments") or {}
    try:
        blocked_response, hook_msg = _run_before_tool_hook(ctx, n, a, request_id)
        if blocked_response is not None:
            return blocked_response

        handler = TOOL_HANDLERS.get(n)
        if handler is None:
            return _unknown_tool_response(request_id, n)

        r = handler(ctx, a)
        return create_text_response(request_id, r, hook_msg)
    except Exception as e:
        return create_error_response(request_id, e)
