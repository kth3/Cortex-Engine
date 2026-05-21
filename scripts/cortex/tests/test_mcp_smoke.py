"""Cortex MCP stdio JSON-RPC smoke test.
신규 tool name 기준 (get_index_status, search_context, get_impact_graph, ...) 검증.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if ROOT.name == ".cortex":
    AGENTS_HOME = ROOT
    WS = ROOT.parent
elif (ROOT / "scripts" / "cortex_mcp.py").exists():
    AGENTS_HOME = ROOT
    WS = ROOT
else:
    AGENTS_HOME = ROOT / ".cortex"
    WS = ROOT
MCP = AGENTS_HOME / "scripts" / "cortex_mcp.py"
MCP_FQN_PREFIX = ".cortex\\scripts" if AGENTS_HOME.name == ".cortex" else "scripts"
RUNTIME_WORKSPACE = Path(os.environ.get("CORTEX_WORKSPACE", str(WS))).resolve()
IMPACT_FQN = f"{MCP_FQN_PREFIX}\\smoke.py::smoke_symbol"

# 신규 tool name 목록 (registry.py TOOLS 순서와 일치)
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

# 구버전 pc_* tool은 신규 엔진에서 존재하면 안 됨
REMOVED_TOOLS = [
    "pc_capsule",
    "pc_index_status",
    "pc_skeleton",
    "pc_impact_graph",
    "pc_logic_flow",
    "pc_git_log",
    "pc_run_pipeline",
    "pc_auto_context",
    "pc_read_with_hash",
    "pc_strict_replace",
    "pc_create_contract",
    "pc_todo_manager",
    "pc_session_sync",
    "pc_memory_write",
    "pc_memory_consolidate",
    "pc_memory_read",
    "pc_save_observation",
    "pc_memory_search_knowledge",
    "pc_index_roots_add",
    "pc_index_roots_list",
    "pc_index_roots_remove",
    "pc_reindex",
]

requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
    # T1: tools/list — 신규 tool 목록 및 schema 확인
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    # T2: get_index_status — schema_version='2'
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "get_index_status", "arguments": {}}},
    # T3: consolidate_memory dry_run=true (기본) — executed=False, 실제 삭제 없음
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "consolidate_memory",
                "arguments": {"new_key": "smoke_dryrun_2026", "category": "insight",
                              "content": "smoke", "old_keys": ["nonexistent_a", "nonexistent_b"]}}},
    # T4: get_impact_graph max_nodes 메타필드
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "get_impact_graph",
                "arguments": {"fqn": IMPACT_FQN,
                              "max_nodes": 10, "max_depth": 2}}},
    # T5: get_session_context
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
     "params": {"name": "get_session_context",
                "arguments": {"token_budget": 1000}}},
    # T6: resolve_symbol — graceful empty response when symbol not in index
    {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
     "params": {"name": "resolve_symbol",
                "arguments": {"name": "nonexistent_symbol_xyz_99"}}},
    # T8: resolve_symbol — fixture DB에 존재하는 심볼 FTS 검색
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
     "params": {"name": "resolve_symbol",
                "arguments": {"name": "smoke_symbol", "limit": 3}}},
    # T9: search_deep_context — capsule_chars 메타필드 확인
    {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
     "params": {"name": "search_deep_context",
                "arguments": {"query": "smoke_symbol", "limit": 3}}},
    # T7: unknown tool — expect error -32601
    {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
     "params": {"name": "pc_capsule", "arguments": {"query": "test"}}},
]

payload = "\n".join(json.dumps(r) for r in requests) + "\n"


def _mcp_commands():
    commands = [("file-entrypoint", [sys.executable, str(MCP)])]
    console = shutil.which("cortex-mcp")
    if console:
        commands.append(("console-entrypoint", [console]))
    return commands


def check(label, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {label}{(': ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def _extract(res):
    if isinstance(res, dict) and "content" in res:
        try:
            return json.loads(res["content"][0]["text"])
        except Exception:
            return res
    return res


def _smoke_env(data_home):
    env = os.environ.copy()
    env["CORTEX_HOME"] = str(AGENTS_HOME)
    env["CORTEX_WORKSPACE"] = str(RUNTIME_WORKSPACE)
    env["CORTEX_DATA_HOME"] = str(data_home)
    env.pop("CORTEX_WORKSPACE_KEY", None)
    return env


def _with_env(env, fn):
    old = {key: os.environ.get(key) for key in ("CORTEX_HOME", "CORTEX_WORKSPACE", "CORTEX_DATA_HOME", "CORTEX_WORKSPACE_KEY")}
    try:
        for key in old:
            if key in env:
                os.environ[key] = env[key]
            else:
                os.environ.pop(key, None)
        return fn()
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _prepare_smoke_db(env):
    def prepare():
        from cortex.storage.connection import get_connection
        from cortex.storage.schema import init_schema

        conn = get_connection(str(RUNTIME_WORKSPACE))
        try:
            init_schema(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT OR REPLACE INTO nodes(
                    id, type, name, fqn, file_path, start_line, end_line,
                    signature, docstring, language, module, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "smoke-node",
                    "function",
                    "smoke_symbol",
                    IMPACT_FQN,
                    "scripts/cortex/tests/test_mcp_smoke.py",
                    1,
                    1,
                    "def smoke_symbol()",
                    "MCP smoke fixture node",
                    "python",
                    "smoke",
                    "SOURCE",
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO memories(
                    key, project_id, category, content, tags, relationships,
                    access_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "smoke.memory",
                    "default",
                    "insight",
                    "MCP smoke fixture memory for get_session_context.",
                    "smoke",
                    "",
                    0,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    _with_env(env, prepare)


def _db_path(env):
    def resolve():
        from cortex.storage.connection import get_db_path

        return Path(get_db_path(str(RUNTIME_WORKSPACE)))

    return _with_env(env, resolve)


def _run_mcp(command, env):
    p = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(WS), env=env,
    )
    try:
        return p.communicate(payload.encode("utf-8"), timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        print("TIMEOUT")
        return out, err


def _parse_results(out):
    results = {}
    for line in out.decode("utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        rid = obj.get("id")
        results[rid] = obj
    return results


def _check_results(results, err, env):
    # T1: tools/list 검증
    tl = results.get(2, {}).get("result", {}).get("tools", [])
    tool_names = [t["name"] for t in tl]
    print(f"[T1] tools/list count={len(tool_names)}")

    # 신규 tool이 모두 존재하는지
    for name in EXPECTED_TOOLS:
        check(f"tool present: {name}", name in tool_names)

    # 구버전 pc_* tool이 모두 제거되었는지
    for name in REMOVED_TOOLS:
        check(f"old tool removed: {name}", name not in tool_names)

    # 모든 tool에 description이 존재하는지
    for t in tl:
        check(f"description non-empty: {t['name']}", bool(t.get("description", "").strip()))

    # 모든 inputSchema가 object 타입인지
    for t in tl:
        check(f"inputSchema is object: {t['name']}", t.get("inputSchema", {}).get("type") == "object")

    # 개별 tool schema 검증
    tool_map = {t["name"]: t for t in tl}

    sc = tool_map.get("search_context")
    if sc:
        sc_props = sc.get("inputSchema", {}).get("properties", {})
        check("search_context.query schema", "query" in sc_props)
        check("search_context.token_budget schema", "token_budget" in sc_props)
        check("search_context no auto_chain (read-only)", "auto_chain" not in sc_props)

    ig = tool_map.get("get_impact_graph")
    if ig:
        ig_props = ig.get("inputSchema", {}).get("properties", {})
        check("get_impact_graph.max_nodes schema", "max_nodes" in ig_props)
        check("get_impact_graph.max_depth default=2", ig_props.get("max_depth", {}).get("default") == 2)
        check("get_impact_graph.direction enum", "enum" in ig_props.get("direction", {}))

    cm = tool_map.get("consolidate_memory")
    if cm:
        cm_props = cm.get("inputSchema", {}).get("properties", {})
        check("consolidate_memory.dry_run default=true", cm_props.get("dry_run", {}).get("default") is True)

    mt = tool_map.get("manage_todo")
    if mt:
        mt_props = mt.get("inputSchema", {}).get("properties", {})
        check("manage_todo.action is enum", "enum" in mt_props.get("action", {}))
        action_enum = mt_props.get("action", {}).get("enum", [])
        check("manage_todo.action enum values", sorted(action_enum) == ["add", "check", "clear"])

    gs = tool_map.get("get_session_context")
    if gs:
        gs_props = gs.get("inputSchema", {}).get("properties", {})
        check("get_session_context.token_budget schema", "token_budget" in gs_props)

    rs = tool_map.get("resolve_symbol")
    if rs:
        rs_props = rs.get("inputSchema", {}).get("properties", {})
        check("resolve_symbol.name schema", "name" in rs_props)
        rs_required = rs.get("inputSchema", {}).get("required", [])
        check("resolve_symbol.name required", "name" in rs_required)

    fep = tool_map.get("find_execution_path")
    if fep:
        fep_props = fep.get("inputSchema", {}).get("properties", {})
        check("find_execution_path.from_fqn schema", "from_fqn" in fep_props)
        check("find_execution_path.to_fqn schema", "to_fqn" in fep_props)

    print()
    # T2: get_index_status — schema_version='2'
    idx = results.get(3, {}).get("result")
    if isinstance(idx, dict):
        if "content" in idx:
            content_text = idx["content"][0]["text"] if idx["content"] else "{}"
            try:
                idx_data = json.loads(content_text)
            except Exception:
                idx_data = {}
        else:
            idx_data = idx
        check("get_index_status schema_version=2", idx_data.get("schema_version") == "2", repr(idx_data.get("schema_version")))

    print()
    # T3: consolidate_memory dry_run=true(default)
    mc_data = _extract(results.get(4, {}).get("result"))
    print(f"[T3] consolidate_memory dry_run=true(default):")
    if isinstance(mc_data, dict):
        check("consolidate_memory dry_run executed=false", mc_data.get("executed") is False)
        check("consolidate_memory dry_run would_delete", mc_data.get("would_delete") == ["nonexistent_a", "nonexistent_b"])
        check("consolidate_memory dry_run would_write present", bool(mc_data.get("would_write")))

    print()
    # T4: get_impact_graph metadata fields
    ig_res = _extract(results.get(5, {}).get("result"))
    print(f"[T4] get_impact_graph response keys = {sorted(ig_res.keys()) if isinstance(ig_res, dict) else 'ERR'}")
    if isinstance(ig_res, dict):
        for k in ("truncated", "limit", "returned_count", "total_seen"):
            print(f"  {k} = {ig_res.get(k)}")
        check("get_impact_graph metadata keys", all(k in ig_res for k in ("truncated", "limit", "returned_count", "total_seen")))
    else:
        check("get_impact_graph metadata keys", False, "non-dict response")

    print()
    # T5: get_session_context
    ac_res = _extract(results.get(7, {}).get("result"))
    if isinstance(ac_res, dict):
        check("get_session_context context key", "context" in ac_res)
        check("get_session_context totalChars key", "totalChars" in ac_res)
        check("get_session_context itemCount key", "itemCount" in ac_res)
    else:
        check("get_session_context response keys", False, "non-dict response")

    print()
    # T6: resolve_symbol graceful empty response
    rs_res = _extract(results.get(8, {}).get("result"))
    if isinstance(rs_res, dict):
        check("resolve_symbol candidates key", "candidates" in rs_res)
        check("resolve_symbol count key", "count" in rs_res)
        # nonexistent symbol → empty candidates
        check("resolve_symbol empty for unknown symbol", rs_res.get("candidates") == [])
        check("resolve_symbol next_suggestion when empty", bool(rs_res.get("next_suggestion")))
    else:
        check("resolve_symbol response keys", False, "non-dict response")

    print()
    # T8: resolve_symbol — fixture에 등록된 "smoke_symbol" FTS 검색
    rs_fixture = _extract(results.get(10, {}).get("result"))
    if isinstance(rs_fixture, dict):
        check("resolve_symbol fixture candidates present", rs_fixture.get("count", 0) > 0)
        cands = rs_fixture.get("candidates", [])
        if cands:
            first = cands[0]
            # 반환 필드 완전성 확인
            for field in ("fqn", "name", "kind", "language", "file_path", "line", "match_reason"):
                check(f"resolve_symbol candidate.{field} present", field in first)
            # match_reason이 허용된 값인지 확인
            valid_reasons = {"exact_fqn", "fts_match", "vector_match"}
            check(
                "resolve_symbol match_reason valid",
                first.get("match_reason") in valid_reasons,
                first.get("match_reason"),
            )
    else:
        check("resolve_symbol fixture response", False, "non-dict response")

    print()
    # T9: search_deep_context — capsule_chars 메타필드 및 구조 검증
    sdc_res = _extract(results.get(11, {}).get("result"))
    if isinstance(sdc_res, dict):
        check("search_deep_context unified_context key", "unified_context" in sdc_res)
        check("search_deep_context capsule key", "capsule" in sdc_res)
        check("search_deep_context capsule_chars key", "capsule_chars" in sdc_res)
        check("search_deep_context capsule_chars is int", isinstance(sdc_res.get("capsule_chars"), int))
        check("search_deep_context impact_summary key", "impact_summary" in sdc_res)
        # capsule이 희박한 경우 chained_memories가 포함될 수 있음 (선택적)
        if sdc_res.get("chained_memories") is not None:
            check(
                "search_deep_context chained_memories is list",
                isinstance(sdc_res["chained_memories"], list),
            )
    else:
        check("search_deep_context response keys", False, "non-dict response")

    print()
    # T7: unknown tool (old pc_capsule) → error -32601
    unknown_res = results.get(9, {})
    check("unknown tool returns error", "error" in unknown_res)
    check("unknown tool error code -32601", unknown_res.get("error", {}).get("code") == -32601)

    print()
    # dry_run이 실제로 DB에 기록하지 않았는지 확인
    conn = sqlite3.connect(str(_db_path(env)))
    row = conn.execute("SELECT key FROM memories WHERE key='smoke_dryrun_2026'").fetchone()
    check("dry_run did not write memory row", not bool(row))
    conn.close()

    if err:
        print()
        print("=== STDERR (last 15 lines) ===")
        for l in err.decode("utf-8", errors="replace").splitlines()[-15:]:
            print(l)


failures = []


def run_smoke():
    global failures
    failures = []
    with tempfile.TemporaryDirectory(prefix="cortex-mcp-smoke-", ignore_cleanup_errors=True) as data_home:
        env = _smoke_env(data_home)
        _prepare_smoke_db(env)
        for label, command in _mcp_commands():
            print("=" * 70)
            print(f"MCP smoke command: {label}")
            out, err = _run_mcp(command, env)
            _check_results(_parse_results(out), err, env)

    if failures:
        print()
        print("FAILED SMOKE CHECKS:")
        for item in failures:
            print(f"  - {item}")
        raise AssertionError(f"MCP smoke failed: {', '.join(failures)}")

    print()
    print("ALL MCP SMOKE CHECKS PASSED")


@pytest.mark.smoke
def test_mcp_stdio_json_rpc_smoke():
    run_smoke()


if __name__ == "__main__":
    try:
        run_smoke()
    except AssertionError:
        sys.exit(1)
