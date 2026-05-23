"""Shared memory helpers for MCP tools."""
from __future__ import annotations

import datetime
import os
import shutil

from cortex import paths as pc_paths
from cortex.hooks import manager as pc_hooks
from cortex.memories import working as pc_mem_mod
from cortex.memories.persistent import PersistentMemoryManager

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
    """현재 프로세스에서 사용하는 persistent memory manager를 lazy init한다."""
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


def _dispatch_after_save_observation(ctx) -> None:
    pc_hooks.dispatch(ctx.workspace, "after_save_observation")


def _save_observation(ctx, args):
    return pc_mem_mod.save_observation(
        ctx.workspace,
        ctx.session_id,
        args.get("obs_type", DEFAULT_OBSERVATION_TYPE),
        args["content"],
        args.get("file_paths", list(DEFAULT_FILE_PATHS)),
    )
