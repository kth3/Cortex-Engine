"""
Cortex MCP Server Main Entrypoint
"""
import sys
import json
import os
import uuid
import threading
import time
from pathlib import Path

def configure_stdio():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# 경로 설정
SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import paths as pc_paths
from cortex.mcp.context import McpContext
from cortex.mcp.registry import list_tools
from cortex.mcp.dispatcher import handle_tools_call

def _find_real_workspace(start_path):
    return str(pc_paths.resolve_workspace(start_path))

WORKSPACE = _find_real_workspace(SCRIPTS_DIR)
SESSION_ID = str(uuid.uuid4())[:8]

CTX = McpContext(workspace=WORKSPACE, session_id=SESSION_ID, scripts_dir=SCRIPTS_DIR)

def handle_request(req):
    m, p, rid = req.get("method"), req.get("params", {}), req.get("id")
    if m == "initialize": return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "Cortex-Hooks", "version": "3.8.0"}}}
    if m == "tools/list": return {"jsonrpc": "2.0", "id": rid, "result": {"tools": list_tools()}}
    if m == "tools/call":
        return handle_tools_call(CTX, p, rid)
    return {"jsonrpc": "2.0", "id": rid, "result": {}} if rid else None

def parent_watcher():
    """부모 프로세스 생존을 감시하는 데드맨 스위치 (다중 실행 시 충돌 방지 위해 직계 부모만 감시)"""
    try:
        import psutil
    except ImportError:
        return

    try:
        ppid = os.getppid()
        parent = psutil.Process(ppid)
    except Exception:
        os._exit(0)
    
    while True:
        try:
            if not parent.is_running() or parent.status() == psutil.STATUS_ZOMBIE:
                os._exit(0)
        except Exception:
            os._exit(0)
        
        try:
            time.sleep(2)
        except Exception:
            pass

def serve():
    watcher = threading.Thread(target=parent_watcher, daemon=True)
    watcher.start()

    try:
        import subprocess
        ctl_script = SCRIPTS_DIR / "cortex" / "cortex_ctl.py"
        if ctl_script.exists():
            subprocess.Popen(
                [sys.executable, str(ctl_script), "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
    except:
        pass

    try:
        while True:
            line = sys.stdin.readline()
            if not line: break
            try:
                req = json.loads(line)
                res = handle_request(req)
                if res: sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n"); sys.stdout.flush()
            except: pass
    finally:
        sys.stderr.write("[Cortex] MCP server terminated.\n")

def main():
    configure_stdio()
    serve()
