"""Session sync record and persistence helpers."""
from __future__ import annotations

import datetime
import os
import yaml

from cortex import paths as pc_paths

from .memory_support import _append_markdown_with_archive, get_storage
from .session_sync_git import _current_branch_and_issues, _recent_modified_files, _session_relationships

TEXT_FILE_ENCODING = "utf-8"

SESSION_MEMORY_NAMESPACE = "default"
SESSION_SYNC_KEY_PREFIX = "session-sync-"
SESSION_SYNC_CATEGORY = "decision"
SESSION_SYNC_TAGS = ("session-sync", "auto-generated", "autonomous-rag")

INBOX_HISTORY_FILE = "inbox.md"
MEMORY_YAML_FILE = "memory.yaml"
YAML_ACTIVE_BRANCH_KEY = "active_branch"
YAML_LAST_SYNC_KEY = "last_sync"

SESSION_LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
YAML_LAST_SYNC_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

def _session_sync_key(ctx):
    return f"{SESSION_SYNC_KEY_PREFIX}{ctx.session_id}"


def _session_sync_payload(key, task_desc, relationships):
    return {
        "key": key,
        "category": SESSION_SYNC_CATEGORY,
        "content": task_desc,
        "tags": list(SESSION_SYNC_TAGS),
        "relationships": relationships,
    }


def _write_session_sync_memory(ctx, data):
    return get_storage(ctx).write(SESSION_MEMORY_NAMESPACE, data)


def _session_log_timestamp():
    return datetime.datetime.now().strftime(SESSION_LOG_TIMESTAMP_FORMAT)


def _session_sync_log_line(task_desc, branch, jira_issues, modified_files):
    now_str = _session_log_timestamp()
    return (
        f"\n- [CONFIRMED] **[SESSION_SYNC]** {now_str} | Branch: {branch} | Issue: {jira_issues}\n"
        f"  - 📝 {task_desc}\n"
        f"  - 📂 Modifies: {len(modified_files)} files\n"
    )


def _append_session_sync_markdown(
    ctx, task_desc, branch, jira_issues, modified_files
) -> None:
    _append_markdown_with_archive(
        ctx,
        INBOX_HISTORY_FILE,
        _session_sync_log_line(task_desc, branch, jira_issues, modified_files),
    )


def _memory_yaml_path(ctx):
    return str(pc_paths.history_dir(ctx.workspace) / MEMORY_YAML_FILE)


def _yaml_last_sync_timestamp():
    return datetime.datetime.now().strftime(YAML_LAST_SYNC_TIMESTAMP_FORMAT)


def _update_memory_yaml_if_exists(ctx, branch) -> None:
    yaml_path = _memory_yaml_path(ctx)
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r", encoding=TEXT_FILE_ENCODING) as yf:
                yaml_data = yaml.safe_load(yf) or {}
            yaml_data[YAML_ACTIVE_BRANCH_KEY] = branch
            yaml_data[YAML_LAST_SYNC_KEY] = _yaml_last_sync_timestamp()
            with open(yaml_path, "w", encoding=TEXT_FILE_ENCODING) as yf:
                yaml.dump(yaml_data, yf, allow_unicode=True, sort_keys=False)
        except Exception:
            pass


def call_sync_session_memory(ctx, args):
    task_desc = args["task_desc"]

    branch, jira_issues = _current_branch_and_issues(ctx.workspace)
    modified_files = _recent_modified_files(ctx.workspace)
    relationships = _session_relationships(branch, jira_issues, modified_files)

    key = _session_sync_key(ctx)
    data = _session_sync_payload(key, task_desc, relationships)

    ok = _write_session_sync_memory(ctx, data)
    _append_session_sync_markdown(ctx, task_desc, branch, jira_issues, modified_files)
    _update_memory_yaml_if_exists(ctx, branch)

    return {
        "success": ok,
        "key": key,
        "extracted_relationships": relationships,
        "markdown_synced": True,
    }
