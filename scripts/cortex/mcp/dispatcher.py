"""
Cortex MCP Tool Dispatcher
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

def handle_tools_call(ctx, params, request_id):
    n, a = params.get("name"), params.get("arguments") or {}
    try:
        hook_msg = ""
        if n in ["pc_strict_replace", "pc_create_contract", "pc_todo_manager", "pc_capsule"]:
            guard_res = pc_hooks.dispatch(ctx.workspace, "before_tool_call", n, json.dumps(a))
            if guard_res and isinstance(guard_res, str):
                if guard_res.startswith("Error:"):
                    return {"jsonrpc": "2.0", "id": request_id, "result": {"isError": True, "content": [{"type": "text", "text": f"Guard Blocked: {guard_res}"}]}}
                elif guard_res.startswith("Info:"):
                    hook_msg = f"[{guard_res}]\n"
                else:
                    hook_msg = f"[Hook: {guard_res}]\n"

        if n == "pc_reindex": r = call_pc_reindex(ctx, a)
        elif n == "pc_index_status": r = call_pc_index_status(ctx, a)
        elif n == "pc_index_roots_list": r = call_pc_index_roots_list(ctx, a)
        elif n == "pc_index_roots_add": r = call_pc_index_roots_add(ctx, a)
        elif n == "pc_index_roots_remove": r = call_pc_index_roots_remove(ctx, a)
        elif n == "pc_capsule": r = call_pc_capsule(ctx, a)
        elif n == "pc_skeleton": r = call_pc_skeleton(ctx, a)
        elif n == "pc_impact_graph": r = call_pc_impact_graph(ctx, a)
        elif n == "pc_logic_flow": r = call_pc_logic_flow(ctx, a)
        elif n == "pc_git_log": r = call_pc_git_log(ctx, a)
        elif n == "pc_run_pipeline": r = call_pc_run_pipeline(ctx, a)
        elif n == "pc_auto_context": r = call_pc_auto_context(ctx, a)
        elif n == "pc_read_with_hash": r = call_pc_read_with_hash(ctx, a)
        elif n == "pc_strict_replace": r = call_strict_replace(ctx, a)
        elif n == "pc_create_contract": r = call_create_contract(ctx, a)
        elif n == "pc_todo_manager": r = call_todo_manager(ctx, a)
        elif n == "pc_session_sync": r = call_pc_session_sync(ctx, a)
        elif n == "pc_memory_write": r = call_pc_memory_write(ctx, a)
        elif n == "pc_memory_consolidate": r = call_pc_memory_consolidate(ctx, a)
        elif n == "pc_memory_read": r = call_pc_memory_read(ctx, a)
        elif n == "pc_save_observation": r = call_save_observation(ctx, a)
        elif n == "pc_memory_search_knowledge": r = call_pc_memory_search_knowledge(ctx, a)
        else: return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown tool: {n}"}}
        
        return create_text_response(request_id, r, hook_msg)
    except Exception as e: return create_error_response(request_id, e)
