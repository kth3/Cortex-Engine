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

def call_pc_session_sync(ctx, args):
    task_desc = args["task_desc"]
    branch = "unknown"
    jira_issues = []
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ctx.workspace).decode().strip()
        match = re.search(r'([A-Z0-9]+-\d+)', branch)
        if match:
            jira_issues.append(match.group(1))
    except:
        pass
    modified_files = []
    try:
        status1 = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=ctx.workspace).decode().strip().split('\n')
        status2 = subprocess.check_output(["git", "log", "-n", "3", "--name-only", "--pretty=format:"], cwd=ctx.workspace).decode().strip().split('\n')
        combined = [f for f in status1 + status2 if f]
        seen = set()
        for f in combined:
            if f not in seen:
                seen.add(f)
                modified_files.append(f)
    except:
        pass
    relationships = {
        "jira_issues": jira_issues,
        "modifies": modified_files[:10],
        "branch": branch
    }
    key = f"session-sync-{ctx.session_id}"
    data = {
        "key": key,
        "category": "decision",
        "content": task_desc,
        "tags": ["session-sync", "auto-generated", "autonomous-rag"],
        "relationships": relationships
    }
    ok = get_storage(ctx).write("default", data)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"\n- [CONFIRMED] **[SESSION_SYNC]** {now_str} | Branch: {branch} | Issue: {jira_issues}\n  - 📝 {task_desc}\n  - 📂 Modifies: {len(modified_files)} files\n"
    _append_markdown_with_archive(ctx, "inbox.md", log_line)
    yaml_path = str(pc_paths.history_dir(ctx.workspace) / "memory.yaml")
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, 'r', encoding='utf-8') as yf:
                yaml_data = yaml.safe_load(yf) or {}
            yaml_data['active_branch'] = branch
            yaml_data['last_sync'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(yaml_path, 'w', encoding='utf-8') as yf:
                yaml.dump(yaml_data, yf, allow_unicode=True, sort_keys=False)
        except Exception:
            pass
    return {"success": ok, "key": key, "extracted_relationships": relationships, "markdown_synced": True}

def call_pc_auto_context(ctx, args):
    token_budget = args.get("token_budget", 2000)
    conn = pc_db.get_connection(ctx.workspace)
    try:
        sections = []
        total_chars = 0
        
        # 1. 최신 decisions
        rows = conn.execute(
            "SELECT key, content, updated_at FROM memories WHERE category = 'decision' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:150]
            entry = f"[decision] {d['key']}: {snippet}"
            if total_chars + len(entry) > token_budget: break
            sections.append(entry)
            total_chars += len(entry)

        # 2. 최신 patterns
        rows = conn.execute(
            "SELECT key, content, updated_at FROM memories WHERE category = 'pattern' ORDER BY updated_at DESC LIMIT 3"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:150]
            entry = f"[pattern] {d['key']}: {snippet}"
            if total_chars + len(entry) > token_budget: break
            sections.append(entry)
            total_chars += len(entry)

        # 3. 인기 항목 (access_count)
        rows = conn.execute(
            "SELECT key, category, content, access_count FROM memories WHERE access_count > 0 ORDER BY access_count DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            d = dict(r)
            snippet = d["content"][:100]
            entry = f"[{d['category']}] {d['key']} (hits:{d['access_count']}): {snippet}"
            if total_chars + len(entry) > token_budget: break
            if not any(d["key"] in s for s in sections):
                sections.append(entry)
                total_chars += len(entry)

        # 추가: HANDOFF 상태 레인의 contract 확인
        board_path = pc_paths.data_dir(ctx.workspace) / "state" / "board.json"
        if board_path.exists():
            try:
                board = json.loads(board_path.read_text(encoding="utf-8"))
                for lid, lane in board.get("lanes", {}).items():
                    if lane.get("contract_id"):
                        entry = f"[contract] lane={lid}: {lane['contract_id']}"
                        sections.append(entry)
                        total_chars += len(entry)
            except Exception:
                pass

        return {
            "context": "\n".join(sections),
            "totalChars": total_chars,
            "itemCount": len(sections)
        }
    finally:
        conn.close()
