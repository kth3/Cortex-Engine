"""Git-backed session sync helpers."""
from __future__ import annotations

import re
import subprocess

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

MAX_RELATIONSHIP_MODIFIED_FILES = 10


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
    except Exception:
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
    except Exception:
        pass
    return modified_files


def _session_relationships(branch, jira_issues, modified_files):
    return {
        "jira_issues": jira_issues,
        "modifies": modified_files[:MAX_RELATIONSHIP_MODIFIED_FILES],
        "branch": branch,
    }
