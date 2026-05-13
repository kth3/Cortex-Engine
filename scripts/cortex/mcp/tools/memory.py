"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import os
import json
import datetime
import shutil
from cortex.persistent_memory import PersistentMemoryManager
from cortex import paths as pc_paths
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex import vector_engine as ve

_storage = None

def get_storage(ctx):
    global _storage
    if _storage is None:
        _storage = PersistentMemoryManager(ctx.workspace)
    return _storage

def _append_markdown_with_archive(ctx, target_filename, content):
    md_path = str(pc_paths.history_dir(ctx.workspace) / target_filename)
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50 * 1024:
        archive_dir = str(pc_paths.history_dir(ctx.workspace) / "archive")
        os.makedirs(archive_dir, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name_part, ext = os.path.splitext(target_filename)
        archive_path = os.path.join(archive_dir, f"{name_part}_{now_str}{ext}")
        shutil.move(md_path, archive_path)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(content)

def call_save_observation(ctx, args):
    res = pc_mem_mod.save_observation(ctx.workspace, ctx.session_id, args.get("obs_type", "insight"), args["content"], args.get("file_paths", []))
    pc_hooks.dispatch(ctx.workspace, "after_save_observation")
    return res

def call_pc_memory_write(ctx, args):
    key = args["key"]
    category = args["category"]
    content = args["content"]
    data = {
        "key": key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    ok = get_storage(ctx).write("default", data)
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule", "protocol"]:
        target_file = "patterns.md"
    if target_file and ok:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {key}\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(ctx, target_file, log_line)
    return {"success": ok, "key": key, "auto_promoted_to": target_file}

def call_pc_memory_consolidate(ctx, args):
    """파편 메모리 병합. dry_run 기본 True — 사용자 승인 없는 자동 삭제 방지."""
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    dry_run = args.get("dry_run", True)

    would_delete = list(old_keys)
    would_write = {
        "key": new_key,
        "category": category,
        "content": content,
        "tags": args.get("tags", []),
        "relationships": args.get("relationships", {}),
    }
    target_file = None
    if category in ["decision", "architecture"]:
        target_file = "decisions.md"
    elif category in ["pattern", "convention", "rule"]:
        target_file = "patterns.md"

    if dry_run:
        return {
            "executed": False,
            "would_delete": would_delete,
            "would_write": would_write,
            "auto_promoted_to": target_file,
            "note": "dry_run=true (default). 실제 병합·삭제 없음. 실행하려면 dry_run=false 명시.",
        }

    st = get_storage(ctx)
    deleted_count = st.delete_many("default", old_keys)
    ok = st.write("default", would_write)
    if target_file and ok:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_line = f"\n### [{now_str}] {new_key} (Consolidated from {len(old_keys)} items)\n- **Category**: {category}\n- **Content**: {content}\n"
        _append_markdown_with_archive(ctx, target_file, log_line)
    return {
        "executed": True,
        "success": ok,
        "consolidated_key": new_key,
        "deleted_old_fragments": deleted_count,
        "auto_promoted_to": target_file,
        "would_delete": would_delete,
        "would_write": would_write,
    }

def call_pc_memory_read(ctx, args):
    return get_storage(ctx).read("default", args["key"])

def call_pc_memory_search_knowledge(ctx, args):
    raw_res = get_storage(ctx).search_knowledge(
        args["query"],
        category=args.get("category"),
        limit=5,
        ve_module=ve,
    )
    return json.dumps(raw_res, ensure_ascii=False, indent=2)
