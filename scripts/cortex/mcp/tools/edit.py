"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import os
from pathlib import Path
from cortex import db as pc_db
from cortex import memory as pc_mem_mod
from cortex.hooks import dispatch
from cortex.editing import read_with_hash, strict_replace, record_edit_event

TEXT_FILE_ENCODING = "utf-8"

EDIT_EVENT_SOURCE = "cortex_mcp"
STRICT_REPLACE_TOOL_NAME = "pc_strict_replace"
EDIT_OBSERVATION_TYPE = "edit"

AFTER_EDIT_HOOK = "after_edit"
AFTER_SAVE_OBSERVATION_HOOK = "after_save_observation"

PATH_VALIDATION_ERROR_PREFIX = "File path validation failed before edit"
READ_BEFORE_EDIT_ERROR_PREFIX = "File read before edit failed"


def _resolve_workspace_file(workspace, file_path) -> str:
    workspace_path = Path(workspace).resolve()
    full_path_obj = (workspace_path / file_path).resolve()
    full_path_obj.relative_to(workspace_path)
    return str(full_path_obj)


def _read_text_file(full_path: str) -> str:
    with open(full_path, "r", encoding=TEXT_FILE_ENCODING) as f:
        return f.read()


def _strict_edit_summary(file_path: str) -> str:
    return f"Strict edit: {file_path}"


def _record_strict_replace_event(ctx, file_path, before_content, after_content) -> None:
    conn = pc_db.get_connection(ctx.workspace)
    try:
        pc_db.init_schema(conn)
        record_edit_event(
            conn,
            workspace=ctx.workspace,
            file_path=file_path,
            before_content=before_content,
            after_content=after_content,
            session_id=ctx.session_id,
            event_source=EDIT_EVENT_SOURCE,
            tool_name=STRICT_REPLACE_TOOL_NAME,
            edit_summary=_strict_edit_summary(file_path),
        )
    finally:
        conn.close()


def _record_successful_strict_replace(ctx, full_path, file_path, before_content) -> None:
    after_content = _read_text_file(full_path)
    _record_strict_replace_event(ctx, file_path, before_content, after_content)


def _dispatch_after_edit(ctx, file_path):
    return dispatch(
        ctx.workspace,
        AFTER_EDIT_HOOK,
        os.path.join(ctx.workspace, file_path),
    )


def _save_strict_edit_observation(ctx, file_path) -> None:
    pc_mem_mod.save_observation(
        ctx.workspace,
        ctx.session_id,
        EDIT_OBSERVATION_TYPE,
        _strict_edit_summary(file_path),
        [file_path],
    )
    dispatch(ctx.workspace, AFTER_SAVE_OBSERVATION_HOOK)


def call_pc_read_with_hash(ctx, args):
    return read_with_hash(ctx.workspace, args["file_path"])


def call_strict_replace(ctx, args):
    file_path = args["file_path"]
    try:
        full_path = _resolve_workspace_file(ctx.workspace, file_path)
    except Exception as e:
        return {"error": f"{PATH_VALIDATION_ERROR_PREFIX}: {e}"}

    try:
        before_content = _read_text_file(full_path)
    except Exception as e:
        return {"error": f"{READ_BEFORE_EDIT_ERROR_PREFIX}: {e}"}

    res = strict_replace(
        ctx.workspace,
        file_path,
        args["old_content"],
        args["new_content"],
    )

    if "success" in res:
        try:
            _record_successful_strict_replace(
                ctx,
                full_path,
                file_path,
                before_content,
            )
        except Exception as e:
            res["event_log_error"] = str(e)

        hook_feedback = _dispatch_after_edit(ctx, file_path)
        if hook_feedback:
            res["hook_feedback"] = hook_feedback

        # observation 저장 및 후속 hook 실행
        _save_strict_edit_observation(ctx, file_path)

    return res
