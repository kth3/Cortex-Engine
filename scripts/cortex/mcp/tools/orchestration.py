"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import hashlib
import sys
from dataclasses import replace
from pathlib import Path

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import paths as pc_paths
from cortex.orchestration import manage_todo, create_contract
from cortex.memories import working as pc_mem_mod
from cortex.hooks import manager as pc_hooks
from cortex.vcs.git_worktree import create_detached_worktree

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
        path_text = str(file_path).strip().replace("\\", "/")
        if not path_text:
            continue

        path = Path(path_text)
        try:
            absolute_path = path.resolve() if path.is_absolute() else (workspace_path / path).resolve()
            path_text = absolute_path.relative_to(workspace_path).as_posix()
        except ValueError:
            path_text = path.as_posix()

        path_text = path_text.casefold()
        if path_text not in seen:
            seen.add(path_text)
            normalized.append(path_text)

    return normalized


def _safe_lane_dirname(lane_id: str) -> str:
    lane_text = str(lane_id)
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_"
        for ch in lane_text
    ).strip("._")
    digest = hashlib.sha1(lane_text.encode("utf-8")).hexdigest()[:8]
    return f"{(safe[:64] or 'lane')}-{digest}"


def _isolation_target_dir(main_workspace: Path, lane_id: str) -> Path:
    return (
        pc_paths.data_home()
        / "isolated_workspaces"
        / pc_paths.workspace_key(main_workspace)
        / _safe_lane_dirname(lane_id)
    )


def _create_git_isolation(ctx, lane_id, files_to_modify, conflicts):
    main_workspace = pc_paths.resolve_workspace(ctx.workspace)
    target_dir = _isolation_target_dir(main_workspace, lane_id)
    isolated_workspace = create_detached_worktree(main_workspace, target_dir)

    import relay

    return relay.register_isolated_workspace(
        lane_id,
        ctx.session_id,
        str(main_workspace),
        str(isolated_workspace),
        files_to_modify,
        conflicts,
    )


def _isolation_notice(isolation):
    conflicts = ", ".join(
        f"{item['path']} held by lane '{item['lane_id']}'"
        for item in isolation.get("conflicts", [])
    )
    return (
        "\n\n[SYSTEM] A file collision was detected. "
        f"You have been routed to an isolated Git worktree at {isolation['isolated_workspace']}. "
        f"Conflicts: {conflicts or 'unknown'}."
    )


def call_create_contract(ctx, args):
    """작업 계약을 생성한다."""
    files_to_modify = _workspace_relative_files(ctx.workspace, args.get("files_to_modify"))
    isolation = None
    contract_ctx = ctx
    instructions = args["instructions"]

    if files_to_modify:
        import relay
        try:
            relay.claim_files_to_modify(args["lane_id"], files_to_modify)
        except relay.FileClaimConflict as exc:
            isolation = _create_git_isolation(
                ctx,
                args["lane_id"],
                files_to_modify,
                exc.conflicts,
            )
            contract_ctx = replace(ctx, workspace=isolation["isolated_workspace"])
            instructions = instructions + _isolation_notice(isolation)

    res = create_contract(
        contract_ctx.workspace,
        contract_ctx.session_id,
        args["lane_id"],
        args["task_name"],
        instructions,
        files_to_modify,
    )
    if isolation:
        res["isolation"] = {"active": True, **isolation}
    _save_contract_observation(contract_ctx, res["contract_id"], res["path"])
    return res
