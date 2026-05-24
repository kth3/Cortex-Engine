"""Rust Cortex MCP stdio JSON-RPC smoke test."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUST_MCP = ROOT / "rust" / "target" / "debug" / (
    "cortex-mcp.exe" if os.name == "nt" else "cortex-mcp"
)
RUNTIME_WORKSPACE = Path(os.environ.get("CORTEX_WORKSPACE", str(ROOT))).resolve()

EXPECTED_TOOLS = [
    "get_index_status",
    "search_context",
    "search_deep_context",
    "get_file_outline",
    "read_file_with_hash",
    "resolve_symbol",
    "get_impact_graph",
    "find_execution_path",
    "get_file_git_history",
    "replace_exact_text",
    "get_session_context",
    "sync_session_memory",
    "write_memory",
    "consolidate_memory",
    "read_memory",
    "save_observation",
    "search_memory",
    "create_task_contract",
    "manage_todo",
]


def _workspace_key(workspace: Path) -> str:
    return hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:12]


def _db_path(data_home: Path) -> Path:
    return data_home / "workspaces" / _workspace_key(RUNTIME_WORKSPACE) / "memories.db"


def _build_rust_mcp() -> None:
    subprocess.run(
        [
            "cargo",
            "build",
            "--manifest-path",
            str(ROOT / "rust" / "Cargo.toml"),
            "-p",
            "cortex-mcp",
        ],
        cwd=ROOT,
        check=True,
    )


def _prepare_db(data_home: Path) -> None:
    db_path = _db_path(data_home)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '2');
            CREATE TABLE IF NOT EXISTS file_cache (
                file_path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                last_indexed_at INTEGER NOT NULL,
                workspace_id TEXT DEFAULT 'default'
            );
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                fqn TEXT NOT NULL,
                file_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                signature TEXT,
                return_type TEXT,
                docstring TEXT,
                is_exported INTEGER DEFAULT 1,
                is_async INTEGER DEFAULT 0,
                is_test INTEGER DEFAULT 0,
                raw_body TEXT,
                skeleton_standard TEXT,
                skeleton_minimal TEXT,
                language TEXT,
                module TEXT DEFAULT 'unknown',
                workspace_id TEXT DEFAULT 'default',
                category TEXT DEFAULT 'SOURCE'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                name, fqn, file_path, content='nodes', content_rowid='rowid'
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'CALLS',
                call_site_line INTEGER,
                UNIQUE(source_id, target_id, type)
            );
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                project_id TEXT DEFAULT 'default',
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                relationships TEXT DEFAULT '{}',
                access_count INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                key, content, category, content='memories', content_rowid='rowid'
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                type TEXT,
                content TEXT,
                file_paths TEXT,
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS file_edit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                before_hash TEXT,
                after_hash TEXT,
                line_range TEXT,
                tool_name TEXT,
                event_sources TEXT,
                session_id TEXT,
                edit_summary TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        now = int(time.time())
        conn.execute(
            """
            INSERT OR REPLACE INTO nodes
            (id, type, name, fqn, file_path, start_line, end_line, signature, raw_body, language, category)
            VALUES (?, 'function', 'smoke_symbol', ?, 'smoke.py', 1, 3, 'def smoke_symbol()', 'def smoke_symbol(): pass', 'python', 'SOURCE')
            """,
            ("node-smoke", "smoke.py::smoke_symbol"),
        )
        rowid = conn.execute("SELECT rowid FROM nodes WHERE id='node-smoke'").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO nodes_fts(rowid, name, fqn, file_path) VALUES (?, ?, ?, ?)",
            (rowid, "smoke_symbol", "smoke.py::smoke_symbol", "smoke.py"),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO memories
            (key, category, content, tags, relationships, created_at, updated_at)
            VALUES ('smoke.memory', 'insight', 'MCP smoke fixture memory.', '[]', '{}', ?, ?)
            """,
            (now, now),
        )
        mem_rowid = conn.execute("SELECT rowid FROM memories WHERE key='smoke.memory'").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO memories_fts(rowid, key, content, category) VALUES (?, ?, ?, ?)",
            (mem_rowid, "smoke.memory", "MCP smoke fixture memory.", "insight"),
        )
        conn.commit()
    finally:
        conn.close()


def _run_mcp(env: dict[str, str], requests: list[dict]) -> list[dict]:
    payload = "\n".join(json.dumps(req) for req in requests) + "\n"
    proc = subprocess.run(
        [str(RUST_MCP)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=20,
        check=True,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _content_json(response: dict) -> dict:
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def test_rust_mcp_stdio_json_rpc_smoke():
    _build_rust_mcp()
    with tempfile.TemporaryDirectory(prefix="cortex-rust-mcp-smoke-") as tmp:
        data_home = Path(tmp)
        _prepare_db(data_home)
        env = os.environ.copy()
        env["CORTEX_WORKSPACE"] = str(RUNTIME_WORKSPACE)
        env["CORTEX_DATA_HOME"] = str(data_home)
        env.pop("CORTEX_WORKSPACE_KEY", None)

        responses = _run_mcp(
            env,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_index_status", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "resolve_symbol", "arguments": {"name": "smoke_symbol"}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "pc_capsule", "arguments": {}},
                },
            ],
        )

    assert responses[0]["result"]["serverInfo"]["name"] == "Cortex-Hooks"
    tools = responses[1]["result"]["tools"]
    assert [tool["name"] for tool in tools] == EXPECTED_TOOLS
    status = _content_json(responses[2])
    assert status["schema_version"] == "2"
    assert status["total_nodes"] == 1
    resolved = _content_json(responses[3])
    assert resolved["count"] >= 1
    assert resolved["candidates"][0]["fqn"] == "smoke.py::smoke_symbol"
    assert responses[4]["error"]["code"] == -32601
