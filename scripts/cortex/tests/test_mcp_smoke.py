"""cortex_mcp.py 직접 stdio JSON-RPC smoke test.
재시작 후 변경된 도구 동작 검증용.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if ROOT.name == ".agents":
    AGENTS_HOME = ROOT
    WS = ROOT.parent
elif (ROOT / "scripts" / "cortex_mcp.py").exists():
    AGENTS_HOME = ROOT
    WS = ROOT
else:
    AGENTS_HOME = ROOT / ".agents"
    WS = ROOT
MCP = AGENTS_HOME / "scripts" / "cortex_mcp.py"
MCP_FQN_PREFIX = ".agents\\scripts" if AGENTS_HOME.name == ".agents" else "scripts"
RUNTIME_WORKSPACE = Path(os.environ.get("CORTEX_WORKSPACE", str(WS))).resolve()
DB_PATH = RUNTIME_WORKSPACE / ".agents" / "data" / "memories.db"
INDEX_ROOTS_TEST_PATH = "src" if (RUNTIME_WORKSPACE / "src").exists() else (AGENTS_HOME / "scripts" / "cortex" / "tests").relative_to(WS).as_posix()


def detect_impact_fqn():
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        try:
            row = conn.execute(
                "SELECT fqn FROM nodes WHERE fqn IS NOT NULL AND fqn != '' ORDER BY LENGTH(fqn) DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return row[0]
    return f"{MCP_FQN_PREFIX}\\cortex_mcp.py::call_pc_capsule"


IMPACT_FQN = detect_impact_fqn()

requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
    # T1: tools/list — 핵심 도구 및 schema 확인
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    # T2: pc_index_status — schema_version='2'
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "pc_index_status", "arguments": {}}},
    # T3: pc_memory_consolidate dry_run=true (기본) — executed=False, 실제 삭제 없음
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "pc_memory_consolidate",
                "arguments": {"new_key": "smoke_dryrun_2026", "category": "insight",
                              "content": "smoke", "old_keys": ["nonexistent_a", "nonexistent_b"]}}},
    # T4: pc_impact_graph max_nodes 메타필드
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "pc_impact_graph",
                "arguments": {"fqn": IMPACT_FQN,
                              "max_nodes": 10, "max_depth": 2}}},
    # T5: index_roots dry-run tool
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
     "params": {"name": "pc_index_roots_add",
                "arguments": {"path": INDEX_ROOTS_TEST_PATH, "dry_run": True}}},
]

payload = "\n".join(json.dumps(r) for r in requests) + "\n"

p = subprocess.Popen(
    [sys.executable, str(MCP)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=str(WS),
)
try:
    out, err = p.communicate(payload.encode("utf-8"), timeout=120)
except subprocess.TimeoutExpired:
    p.kill()
    out, err = p.communicate()
    print("TIMEOUT")

print("=" * 70)
failures = []


def check(label, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {label}{(': ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


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

# T1: tools/list 검증
tl = results.get(2, {}).get("result", {}).get("tools", [])
tool_names = [t["name"] for t in tl]
print(f"[T1] tools/list count={len(tool_names)}")
check("pc_capsule present", 'pc_capsule' in tool_names)
check("pc_index_roots_add present", 'pc_index_roots_add' in tool_names)
check("pc_index_roots_list present", 'pc_index_roots_list' in tool_names)
cap = next((t for t in tl if t["name"] == "pc_capsule"), None)
if cap:
    cap_props = cap.get("inputSchema", {}).get("properties", {})
    check("pc_capsule.auto_chain schema", 'auto_chain' in cap_props)
    check("pc_capsule.token_budget schema", 'token_budget' in cap_props)
ig = next((t for t in tl if t["name"] == "pc_impact_graph"), None)
if ig:
    ig_props = ig.get("inputSchema", {}).get("properties", {})
    check("pc_impact_graph.max_nodes schema", 'max_nodes' in ig_props)
    check("pc_impact_graph.max_depth default", ig_props.get('max_depth', {}).get('default') == 2)
mc = next((t for t in tl if t["name"] == "pc_memory_consolidate"), None)
if mc:
    mc_props = mc.get("inputSchema", {}).get("properties", {})
    check("pc_memory_consolidate.dry_run default", mc_props.get('dry_run', {}).get('default') is True)
ri = next((t for t in tl if t["name"] == "pc_reindex"), None)
if ri:
    check("pc_reindex destructive warning", 'DESTRUCTIVE' in ri.get('description', ''))

# T2: pc_index_status
print()
idx = results.get(3, {}).get("result")
if isinstance(idx, dict):
    # MCP wraps result in content-style or direct dict; handle both
    if "content" in idx:
        content_text = idx["content"][0]["text"] if idx["content"] else "{}"
        try:
            idx_data = json.loads(content_text)
        except Exception:
            idx_data = {}
    else:
        idx_data = idx
    check("pc_index_status schema_version", idx_data.get('schema_version') == '2', repr(idx_data.get('schema_version')))

# T3: dry_run consolidate
print()
mc_res = results.get(4, {}).get("result")
def _extract(res):
    if isinstance(res, dict) and "content" in res:
        try:
            return json.loads(res["content"][0]["text"])
        except Exception:
            return res
    return res
mc_data = _extract(mc_res)
print(f"[T3] memory_consolidate dry_run=true(default) 응답:")
if isinstance(mc_data, dict):
    check("memory_consolidate dry_run executed false", mc_data.get('executed') is False)
    check("memory_consolidate dry_run would_delete", mc_data.get('would_delete') == ['nonexistent_a', 'nonexistent_b'])
    check("memory_consolidate dry_run would_write present", bool(mc_data.get('would_write')))

# T4: impact_graph 메타필드
print()
ig_res = _extract(results.get(5, {}).get("result"))
print(f"[T4] impact_graph 응답 키 = {sorted(ig_res.keys()) if isinstance(ig_res, dict) else 'ERR'}")
if isinstance(ig_res, dict):
    for k in ("truncated", "limit", "returned_count", "total_seen"):
        print(f"  {k} = {ig_res.get(k)}")
    check("impact_graph metadata keys", all(k in ig_res for k in ("truncated", "limit", "returned_count", "total_seen")))
else:
    check("impact_graph metadata keys", False, "non-dict response")

# T5: index_roots add dry-run
print()
ir_res = _extract(results.get(6, {}).get("result"))
if isinstance(ir_res, dict):
    check("index_roots_add dry_run executed false", ir_res.get('executed') is False)
    check("index_roots_add scan_count present", isinstance(ir_res.get('scan_count'), int))

# 후속 검증: DB에 smoke_dryrun_2026이 실제로 없어야 함 (dry_run 안전성)
print()
conn = sqlite3.connect(str(DB_PATH))
row = conn.execute("SELECT key FROM memories WHERE key='smoke_dryrun_2026'").fetchone()
check("dry_run did not write memory row", not bool(row))
conn.close()

if err:
    print()
    print("=== STDERR (last 15 lines) ===")
    for l in err.decode("utf-8", errors="replace").splitlines()[-15:]:
        print(l)

if failures:
    print()
    print("FAILED SMOKE CHECKS:")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)

print()
print("ALL MCP SMOKE CHECKS PASSED")
