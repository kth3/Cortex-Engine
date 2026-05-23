"""MCP memory tool handlers."""
from __future__ import annotations

import json

from cortex.embeddings import provider as ve

from .memory_core import (
    DEFAULT_DRY_RUN,
    MEMORY_NAMESPACE,
    SEARCH_KNOWLEDGE_LIMIT,
    _append_promoted_memory_log,
    _append_markdown_with_archive,
    _dispatch_after_save_observation,
    _memory_payload,
    _target_file_for_consolidate_category,
    _target_file_for_write_category,
    _save_observation,
    get_storage,
)


def call_save_observation(ctx, args):
    res = _save_observation(ctx, args)
    _dispatch_after_save_observation(ctx)
    return res


def call_write_memory(ctx, args):
    key = args["key"]
    category = args["category"]
    content = args["content"]
    data = _memory_payload(key, category, content, args)

    ok = get_storage(ctx).write(MEMORY_NAMESPACE, data)
    target_file = _target_file_for_write_category(category)

    if target_file and ok:
        _append_promoted_memory_log(ctx, target_file, key, category, content)

    return {"success": ok, "key": key, "auto_promoted_to": target_file}


def call_consolidate_memory(ctx, args):
    """파편 메모리 병합. dry_run 기본 True — 사용자 승인 없는 자동 삭제 방지."""
    new_key = args["new_key"]
    category = args["category"]
    content = args["content"]
    old_keys = args["old_keys"]
    dry_run = args.get("dry_run", DEFAULT_DRY_RUN)

    would_delete = list(old_keys)
    would_write = _memory_payload(new_key, category, content, args)
    target_file = _target_file_for_consolidate_category(category)

    if dry_run:
        return {
            "executed": False,
            "would_delete": would_delete,
            "would_write": would_write,
            "auto_promoted_to": target_file,
            "note": "dry_run=true (default). 실제 병합·삭제 없음. 실행하려면 dry_run=false 명시.",
        }

    st = get_storage(ctx)
    deleted_count = st.delete_many(MEMORY_NAMESPACE, old_keys)
    ok = st.write(MEMORY_NAMESPACE, would_write)
    if target_file and ok:
        title = f"{new_key} (Consolidated from {len(old_keys)} items)"
        _append_promoted_memory_log(ctx, target_file, title, category, content)

    return {
        "executed": True,
        "success": ok,
        "consolidated_key": new_key,
        "deleted_old_fragments": deleted_count,
        "auto_promoted_to": target_file,
        "would_delete": would_delete,
        "would_write": would_write,
    }


def call_read_memory(ctx, args):
    return get_storage(ctx).read(MEMORY_NAMESPACE, args["key"])


def call_search_memory(ctx, args):
    raw_res = get_storage(ctx).search_knowledge(
        args["query"],
        category=args.get("category"),
        limit=SEARCH_KNOWLEDGE_LIMIT,
        ve_module=ve,
    )
    return json.dumps(raw_res, ensure_ascii=False, indent=2)
