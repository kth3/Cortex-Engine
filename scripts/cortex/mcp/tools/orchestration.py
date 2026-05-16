"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import sys
from pathlib import Path

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.orchestration import manage_todo, create_contract
from cortex.memories import working as pc_mem_mod
from cortex.hooks import manager as pc_hooks

CONTRACT_OBSERVATION_CATEGORY = "decision"
AFTER_SAVE_OBSERVATION_HOOK = "after_save_observation"


def _contract_observation_message(contract_id: str) -> str:
    return f"Contract created: {contract_id}"


def _save_contract_observation(ctx, contract_id: str, contract_path: str) -> None:
    pc_mem_mod.save_observation(
        ctx.workspace,
        ctx.session_id,
        CONTRACT_OBSERVATION_CATEGORY,
        _contract_observation_message(contract_id),
        [contract_path],
    )
    pc_hooks.dispatch(ctx.workspace, AFTER_SAVE_OBSERVATION_HOOK)


def call_todo_manager(ctx, args):
    """manages todo list"""
    return manage_todo(
        ctx.workspace, args["action"], args.get("task"), args.get("task_id")
    )


def _workspace_relative_files(workspace, files):
    workspace_path = Path(workspace).resolve()
    normalized = []
    seen = set()

    for file_path in files or []:
        path_text = str(file_path).strip()
        if not path_text:
            continue

        path = Path(path_text)
        try:
            absolute_path = path.resolve() if path.is_absolute() else (workspace_path / path).resolve()
            path_text = absolute_path.relative_to(workspace_path).as_posix()
        except ValueError:
            path_text = path.as_posix()

        if sys.platform.startswith("win"):
            path_text = path_text.casefold()
        if path_text not in seen:
            seen.add(path_text)
            normalized.append(path_text)

    return normalized


def call_create_contract(ctx, args):
    """작업 계약을 생성한다."""
    files_to_modify = _workspace_relative_files(ctx.workspace, args.get("files_to_modify"))
    if files_to_modify:
        import relay
        relay.claim_files_to_modify(args["lane_id"], files_to_modify)

    res = create_contract(
        ctx.workspace,
        ctx.session_id,
        args["lane_id"],
        args["task_name"],
        args["instructions"],
        files_to_modify,
    )
    _save_contract_observation(ctx, res["contract_id"], res["path"])
    return res
