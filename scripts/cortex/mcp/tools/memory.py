"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import os
import json
import datetime
import shutil
from cortex.memories.persistent import PersistentMemoryManager
from cortex import paths as pc_paths
from cortex import memory as pc_mem_mod
from cortex import hooks_manager as pc_hooks
from cortex import vector_engine as ve

MEMORY_NAMESPACE = "default"

DEFAULT_OBSERVATION_TYPE = "insight"
DEFAULT_FILE_PATHS = ()
DEFAULT_TAGS = ()
DEFAULT_RELATIONSHIPS = {}
DEFAULT_DRY_RUN = True

HISTORY_ARCHIVE_DIRNAME = "archive"
HISTORY_ARCHIVE_THRESHOLD_BYTES = 50 * 1024
ARCHIVE_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
MARKDOWN_DATE_FORMAT = "%Y-%m-%d"

DECISIONS_HISTORY_FILE = "decisions.md"
PATTERNS_HISTORY_FILE = "patterns.md"

WRITE_DECISION_CATEGORIES = frozenset({"decision", "architecture"})
WRITE_PATTERN_CATEGORIES = frozenset({"pattern", "convention", "rule", "protocol"})

CONSOLIDATE_DECISION_CATEGORIES = frozenset({"decision", "architecture"})
CONSOLIDATE_PATTERN_CATEGORIES = frozenset({"pattern", "convention", "rule"})

SEARCH_KNOWLEDGE_LIMIT = 5

_storage = None


def get_storage(ctx):
    """현재 프로세스에서 사용하는 persistent memory manager를 lazy init한다.

    기존 동작은 단일 전역 cache이므로, 이번 리팩터링에서는 workspace별 cache로
    확장하지 않고 호출 계약만 보존한다.
    """
    global _storage
    if _storage is None:
        _storage = PersistentMemoryManager(ctx.workspace)
    return _storage


def _history_markdown_path(ctx, target_filename):
    return str(pc_paths.history_dir(ctx.workspace) / target_filename)


def _should_archive_markdown(md_path):
    return (
        os.path.exists(md_path)
        and os.path.getsize(md_path) > HISTORY_ARCHIVE_THRESHOLD_BYTES
    )


def _archive_markdown_file(ctx, md_path, target_filename) -> None:
    archive_dir = str(pc_paths.history_dir(ctx.workspace) / HISTORY_ARCHIVE_DIRNAME)
    os.makedirs(archive_dir, exist_ok=True)
    now_str = datetime.datetime.now().strftime(ARCHIVE_TIMESTAMP_FORMAT)
    name_part, ext = os.path.splitext(target_filename)
    archive_path = os.path.join(archive_dir, f"{name_part}_{now_str}{ext}")
    shutil.move(md_path, archive_path)


def _append_markdown_with_archive(ctx, target_filename, content):
    md_path = _history_markdown_path(ctx, target_filename)
    if _should_archive_markdown(md_path):
        _archive_markdown_file(ctx, md_path, target_filename)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(content)


def _memory_payload(key, category, content, args):
    return {
        "key": key,
        "category": category,
        "content": content,
        "tags": args.get("tags", list(DEFAULT_TAGS)),
        "relationships": args.get("relationships", dict(DEFAULT_RELATIONSHIPS)),
    }


def _target_file_for_write_category(category):
    if category in WRITE_DECISION_CATEGORIES:
        return DECISIONS_HISTORY_FILE
    if category in WRITE_PATTERN_CATEGORIES:
        return PATTERNS_HISTORY_FILE
    return None


def _target_file_for_consolidate_category(category):
    if category in CONSOLIDATE_DECISION_CATEGORIES:
        return DECISIONS_HISTORY_FILE
    if category in CONSOLIDATE_PATTERN_CATEGORIES:
        return PATTERNS_HISTORY_FILE
    return None


def _markdown_date():
    return datetime.datetime.now().strftime(MARKDOWN_DATE_FORMAT)


def _memory_log_line(title, category, content):
    now_str = _markdown_date()
    return f"\n### [{now_str}] {title}\n- **Category**: {category}\n- **Content**: {content}\n"


def _append_promoted_memory_log(ctx, target_file, title, category, content) -> None:
    log_line = _memory_log_line(title, category, content)
    _append_markdown_with_archive(ctx, target_file, log_line)


def call_save_observation(ctx, args):
    res = pc_mem_mod.save_observation(
        ctx.workspace,
        ctx.session_id,
        args.get("obs_type", DEFAULT_OBSERVATION_TYPE),
        args["content"],
        args.get("file_paths", list(DEFAULT_FILE_PATHS)),
    )
    pc_hooks.dispatch(ctx.workspace, "after_save_observation")
    return res


def call_pc_memory_write(ctx, args):
    key = args["key"]
    category = args["category"]
    content = args["content"]
    data = _memory_payload(key, category, content, args)

    ok = get_storage(ctx).write(MEMORY_NAMESPACE, data)
    target_file = _target_file_for_write_category(category)

    if target_file and ok:
        _append_promoted_memory_log(ctx, target_file, key, category, content)

    return {"success": ok, "key": key, "auto_promoted_to": target_file}


def call_pc_memory_consolidate(ctx, args):
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


def call_pc_memory_read(ctx, args):
    return get_storage(ctx).read(MEMORY_NAMESPACE, args["key"])


def call_pc_memory_search_knowledge(ctx, args):
    raw_res = get_storage(ctx).search_knowledge(
        args["query"],
        category=args.get("category"),
        limit=SEARCH_KNOWLEDGE_LIMIT,
        ve_module=ve,
    )
    return json.dumps(raw_res, ensure_ascii=False, indent=2)
