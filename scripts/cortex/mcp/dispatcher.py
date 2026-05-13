"""
Cortex MCP Tool Dispatcher

- 책임: 클라이언트(LLM)가 보낸 MCP Tool 호출 요청을 파싱하여, 적절한 내부 도메인 함수로 라우팅한다.
- 주의: 이 모듈은 MCP tool routing과 response format 생성의 계약을 엄격히 지켜야 하며, 도메인 로직을 직접 구현하지 않는다.
"""
import json
from cortex import hooks_manager as pc_hooks
from cortex.mcp.response import create_text_response, create_error_response

from cortex.mcp.tools.indexing import (
    call_pc_reindex, call_pc_index_status, call_pc_index_roots_list,
    call_pc_index_roots_add, call_pc_index_roots_remove
)
from cortex.mcp.tools.search import (
    call_pc_capsule, call_pc_skeleton, call_pc_impact_graph,
    call_pc_logic_flow, call_pc_run_pipeline
)
from cortex.mcp.tools.edit import (
    call_pc_read_with_hash, call_strict_replace
)
from cortex.mcp.tools.git import call_pc_git_log
from cortex.mcp.tools.memory import (
    call_save_observation, call_pc_memory_write, call_pc_memory_consolidate,
    call_pc_memory_read, call_pc_memory_search_knowledge
)
from cortex.mcp.tools.session import (
    call_pc_auto_context, call_pc_session_sync
)
from cortex.mcp.tools.orchestration import (
    call_todo_manager, call_create_contract
)

# Guard hook은 기존에 보호하던 쓰기성/오케스트레이션 tool에만 적용한다.
GUARDED_TOOL_NAMES = frozenset(
    {
        "pc_strict_replace",
        "pc_create_contract",
        "pc_todo_manager",
        "pc_capsule",
    }
)

# Tool 이름과 내부 handler의 매핑을 한 곳에 고정해 누락/중복을 줄인다.
TOOL_HANDLERS = {
    "pc_reindex": call_pc_reindex,
    "pc_index_status": call_pc_index_status,
    "pc_index_roots_list": call_pc_index_roots_list,
    "pc_index_roots_add": call_pc_index_roots_add,
    "pc_index_roots_remove": call_pc_index_roots_remove,
    "pc_capsule": call_pc_capsule,
    "pc_skeleton": call_pc_skeleton,
    "pc_impact_graph": call_pc_impact_graph,
    "pc_logic_flow": call_pc_logic_flow,
    "pc_git_log": call_pc_git_log,
    "pc_run_pipeline": call_pc_run_pipeline,
    "pc_auto_context": call_pc_auto_context,
    "pc_read_with_hash": call_pc_read_with_hash,
    "pc_strict_replace": call_strict_replace,
    "pc_create_contract": call_create_contract,
    "pc_todo_manager": call_todo_manager,
    "pc_session_sync": call_pc_session_sync,
    "pc_memory_write": call_pc_memory_write,
    "pc_memory_consolidate": call_pc_memory_consolidate,
    "pc_memory_read": call_pc_memory_read,
    "pc_save_observation": call_save_observation,
    "pc_memory_search_knowledge": call_pc_memory_search_knowledge,
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
