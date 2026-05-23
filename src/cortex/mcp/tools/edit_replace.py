"""Exact text replacement MCP handler."""
from __future__ import annotations

import os
from pathlib import Path

from cortex import storage as pc_db
from cortex.editing import record_edit_event, strict_replace
from cortex.hooks import dispatch
from cortex.memories import working as pc_mem_mod

TEXT_FILE_ENCODING = "utf-8"

EDIT_EVENT_SOURCE = "cortex_mcp"
STRICT_REPLACE_TOOL_NAME = "replace_exact_text"
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


def call_replace_exact_text(ctx, args):
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

        _save_strict_edit_observation(ctx, file_path)

    return res
