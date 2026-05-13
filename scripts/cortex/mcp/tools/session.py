"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
import re
import os
import json
import yaml
import datetime
import subprocess
from cortex import db as pc_db
from cortex import paths as pc_paths
from cortex.mcp.tools.memory import get_storage, _append_markdown_with_archive

TEXT_FILE_ENCODING = "utf-8"

DEFAULT_BRANCH_NAME = "unknown"
JIRA_ISSUE_PATTERN = r"([A-Z0-9]+-\d+)"

GIT_BRANCH_COMMAND = ("git", "rev-parse", "--abbrev-ref", "HEAD")
GIT_DIFF_NAMES_COMMAND = ("git", "diff", "--name-only", "HEAD")
GIT_RECENT_LOG_NAMES_COMMAND = (
    "git",
    "log",
    "-n",
    "3",
    "--name-only",
    "--pretty=format:",
)

SESSION_MEMORY_NAMESPACE = "default"
SESSION_SYNC_KEY_PREFIX = "session-sync-"
SESSION_SYNC_CATEGORY = "decision"
SESSION_SYNC_TAGS = ("session-sync", "auto-generated", "autonomous-rag")
MAX_RELATIONSHIP_MODIFIED_FILES = 10

INBOX_HISTORY_FILE = "inbox.md"
MEMORY_YAML_FILE = "memory.yaml"
YAML_ACTIVE_BRANCH_KEY = "active_branch"
YAML_LAST_SYNC_KEY = "last_sync"

SESSION_LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
YAML_LAST_SYNC_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET = 2000
AUTO_CONTEXT_DECISION_LIMIT = 5
AUTO_CONTEXT_PATTERN_LIMIT = 3
AUTO_CONTEXT_POPULAR_LIMIT = 5
AUTO_CONTEXT_STANDARD_SNIPPET_CHARS = 150
AUTO_CONTEXT_POPULAR_SNIPPET_CHARS = 100

AUTO_CONTEXT_DECISION_CATEGORY = "decision"
AUTO_CONTEXT_PATTERN_CATEGORY = "pattern"

SQL_RECENT_DECISIONS = (
    "SELECT key, content, updated_at FROM memories "
    f"WHERE category = '{AUTO_CONTEXT_DECISION_CATEGORY}' "
    f"ORDER BY updated_at DESC LIMIT {AUTO_CONTEXT_DECISION_LIMIT}"
)
SQL_RECENT_PATTERNS = (
    "SELECT key, content, updated_at FROM memories "
    f"WHERE category = '{AUTO_CONTEXT_PATTERN_CATEGORY}' "
    f"ORDER BY updated_at DESC LIMIT {AUTO_CONTEXT_PATTERN_LIMIT}"
)
SQL_POPULAR_MEMORIES = (
    "SELECT key, category, content, access_count FROM memories "
    "WHERE access_count > 0 "
    f"ORDER BY access_count DESC LIMIT {AUTO_CONTEXT_POPULAR_LIMIT}"
)

STATE_DIRNAME = "state"
BOARD_JSON_FILE = "board.json"
BOARD_LANES_KEY = "lanes"
CONTRACT_ID_KEY = "contract_id"


def _git_output_text(workspace, command) -> str:
    return subprocess.check_output(list(command), cwd=workspace).decode().strip()


def _git_output_lines(workspace, command):
    text = _git_output_text(workspace, command)
    return text.split("\n")


def _extract_jira_issues(branch):
    jira_issues = []
    match = re.search(JIRA_ISSUE_PATTERN, branch)
    if match:
        jira_issues.append(match.group(1))
    return jira_issues


def _current_branch_and_issues(workspace):
    branch = DEFAULT_BRANCH_NAME
    jira_issues = []
    try:
        branch = _git_output_text(workspace, GIT_BRANCH_COMMAND)
        jira_issues = _extract_jira_issues(branch)
    except:
        pass
    return branch, jira_issues


def _unique_nonempty_files(file_names):
    unique_files = []
    seen = set()
    for file_name in file_names:
        if file_name and file_name not in seen:
            seen.add(file_name)
            unique_files.append(file_name)
    return unique_files


def _recent_modified_files(workspace):
    modified_files = []
    try:
        status1 = _git_output_lines(workspace, GIT_DIFF_NAMES_COMMAND)
        status2 = _git_output_lines(workspace, GIT_RECENT_LOG_NAMES_COMMAND)
        modified_files = _unique_nonempty_files(status1 + status2)
    except:
        pass
    return modified_files


def _session_relationships(branch, jira_issues, modified_files):
    return {
        "jira_issues": jira_issues,
        "modifies": modified_files[:MAX_RELATIONSHIP_MODIFIED_FILES],
        "branch": branch,
    }


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


def call_pc_session_sync(ctx, args):
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


def _fetch_rows(conn, sql):
    return conn.execute(sql).fetchall()


def _append_entry_with_budget(sections, total_chars, entry, token_budget):
    if total_chars + len(entry) > token_budget:
        return total_chars, False
    sections.append(entry)
    return total_chars + len(entry), True


def _recent_memory_entry(
    row, category, snippet_chars=AUTO_CONTEXT_STANDARD_SNIPPET_CHARS
):
    data = dict(row)
    snippet = data["content"][:snippet_chars]
    return f"[{category}] {data['key']}: {snippet}"


def _popular_memory_entry(row):
    data = dict(row)
    snippet = data["content"][:AUTO_CONTEXT_POPULAR_SNIPPET_CHARS]
    return (
        f"[{data['category']}] {data['key']} (hits:{data['access_count']}): {snippet}",
        data["key"],
    )


def _append_recent_memory_sections(
    conn, sections, total_chars, token_budget, sql, category
):
    rows = _fetch_rows(conn, sql)
    for row in rows:
        entry = _recent_memory_entry(row, category)
        total_chars, added = _append_entry_with_budget(
            sections,
            total_chars,
            entry,
            token_budget,
        )
        if not added:
            break
    return total_chars


def _append_popular_memory_sections(conn, sections, total_chars, token_budget):
    rows = _fetch_rows(conn, SQL_POPULAR_MEMORIES)
    for row in rows:
        entry, key = _popular_memory_entry(row)
        if total_chars + len(entry) > token_budget:
            break
        if not any(key in section for section in sections):
            sections.append(entry)
            total_chars += len(entry)
    return total_chars


def _board_path(ctx):
    return pc_paths.data_dir(ctx.workspace) / STATE_DIRNAME / BOARD_JSON_FILE


def _append_contract_context(ctx, sections, total_chars):
    board_path = _board_path(ctx)
    if board_path.exists():
        try:
            board = json.loads(board_path.read_text(encoding=TEXT_FILE_ENCODING))
            for lane_id, lane in board.get(BOARD_LANES_KEY, {}).items():
                if lane.get(CONTRACT_ID_KEY):
                    entry = f"[contract] lane={lane_id}: {lane[CONTRACT_ID_KEY]}"
                    sections.append(entry)
                    total_chars += len(entry)
        except Exception:
            pass
    return total_chars


def call_pc_auto_context(ctx, args):
    token_budget = args.get("token_budget", DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET)
    conn = pc_db.get_connection(ctx.workspace)
    try:
        sections = []
        total_chars = 0

        total_chars = _append_recent_memory_sections(
            conn,
            sections,
            total_chars,
            token_budget,
            SQL_RECENT_DECISIONS,
            AUTO_CONTEXT_DECISION_CATEGORY,
        )
        total_chars = _append_recent_memory_sections(
            conn,
            sections,
            total_chars,
            token_budget,
            SQL_RECENT_PATTERNS,
            AUTO_CONTEXT_PATTERN_CATEGORY,
        )
        total_chars = _append_popular_memory_sections(
            conn,
            sections,
            total_chars,
            token_budget,
        )
        total_chars = _append_contract_context(ctx, sections, total_chars)

        return {
            "context": "\n".join(sections),
            "totalChars": total_chars,
            "itemCount": len(sections),
        }
    finally:
        conn.close()
