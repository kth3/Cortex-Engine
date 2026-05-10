import os
from pathlib import Path
from cortex import db as pc_db
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex.edit_engine import read_with_hash, strict_replace, record_edit_event

def call_pc_read_with_hash(ctx, args):
    return read_with_hash(ctx.workspace, args["file_path"])

def call_strict_replace(ctx, args):
    file_path = args["file_path"]
    try:
        full_path_obj = (Path(ctx.workspace) / file_path).resolve()
        full_path_obj.relative_to(Path(ctx.workspace).resolve())
        full_path = str(full_path_obj)
    except Exception as e:
        return {"error": f"File path validation failed before edit: {e}"}

    before_content = None
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            before_content = f.read()
    except Exception as e:
        return {"error": f"File read before edit failed: {e}"}

    res = strict_replace(ctx.workspace, file_path, args["old_content"], args["new_content"])
    if "success" in res:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                after_content = f.read()
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
                    event_source="cortex_mcp",
                    tool_name="pc_strict_replace",
                    edit_summary=f"Strict edit: {file_path}",
                )
            finally:
                conn.close()
        except Exception as e:
            # 편집은 이미 디스크에 반영된 뒤이므로, 감사/관측용 DB 기록 실패를 편집 실패로
            # 되돌리면 실제 상태와 응답이 어긋난다. success는 유지하고 별도 필드로 노출해
            # 운영자가 로깅 경로 문제만 분리해서 추적할 수 있게 한다.
            res["event_log_error"] = str(e)

        # [Lifecycle Hook] After Edit Hook 실행
        hook_feedback = pc_hooks.dispatch(ctx.workspace, "after_edit", os.path.join(ctx.workspace, file_path))
        if hook_feedback: res["hook_feedback"] = hook_feedback
        
        pc_mem_mod.save_observation(ctx.workspace, ctx.session_id, "edit", f"Strict edit: {file_path}", [file_path])
        
        # [Lifecycle Hook] After Save Observation (자동 추출)
        pc_hooks.dispatch(ctx.workspace, "after_save_observation")
    return res
