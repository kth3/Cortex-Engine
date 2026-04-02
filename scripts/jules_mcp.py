#!/usr/bin/env python3
"""
Jules AI 전용 MCP 서버
코드 리뷰 요청 기능을 담당합니다.
"""
import sys
import json
import urllib.request
from pathlib import Path

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

def load_env():
    if not ENV_PATH.exists(): return {}
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env()

def jules_review(commit_id, diff_content, instructions):
    api_key = ENV.get("JULES_API_KEY")
    if not api_key:
        raise ValueError("JULES_API_KEY is not set in .env")
    url = "https://jules.googleapis.com/v1alpha/sessions"
    headers = {"X-Goog-Api-Key": str(api_key), "Content-Type": "application/json"}
    
    session_payload = {"prompt": f"Review {commit_id}", "title": f"Review - {commit_id}"}
    req = urllib.request.Request(url, data=json.dumps(session_payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        sid = json.loads(resp.read().decode())['id']
    
    msg_url = f"{url}/{sid}:sendMessage"
    msg_payload = {"prompt": f"{instructions}\n\n--- Diff ---\n{diff_content[:15000]}"}
    req = urllib.request.Request(msg_url, data=json.dumps(msg_payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        return f"Jules AI 리뷰 요청 성공 (Session ID: {sid})"

TOOLS = [
    {
        "name": "jules_request_review",
        "description": "Jules AI에게 코드 리뷰를 요청합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "commit_id": {"type": "string", "description": "리뷰할 커밋 ID"},
                "diff_content": {"type": "string", "description": "코드 diff 내용"},
                "instructions": {"type": "string", "description": "리뷰 지침"}
            },
            "required": ["commit_id", "diff_content", "instructions"]
        }
    }
]

def handle_request(req):
    method = req.get("method")
    params = req.get("params", {})
    rid = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Jules-AI-MCP", "version": "1.0.0"}
            }
        }
    
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        try:
            if tool_name == "jules_request_review":
                res = jules_review(args["commit_id"], args["diff_content"], args["instructions"])
                return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(res)}]}}
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Error: {str(e)}"}]}}

    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return None

def serve():
    sys.stderr.write("[jules-mcp] Server starting...\n")
    sys.stderr.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            res = handle_request(req)
            if res:
                sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"[jules-mcp] Error: {str(e)}\n")
            sys.stderr.flush()

if __name__ == "__main__":
    serve()
