"""
Cortex MCP: Orchestration Tool Handlers
"""
import sys
from pathlib import Path

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.orchestrator import manage_todo, create_contract
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks

def call_todo_manager(ctx, args):
    """manages todo list"""
    return manage_todo(ctx.workspace, args["action"], args.get("task"), args.get("task_id"))

def call_create_contract(ctx, args):
    """creates a contract for a task"""
    res = create_contract(ctx.workspace, ctx.session_id, args["lane_id"], args["task_name"], args["instructions"], args.get("files_to_modify"))
    pc_mem_mod.save_observation(ctx.workspace, ctx.session_id, "decision", f"Contract created: {res['contract_id']}", [res['path']])
    pc_hooks.dispatch(ctx.workspace, "after_save_observation")
    return res
