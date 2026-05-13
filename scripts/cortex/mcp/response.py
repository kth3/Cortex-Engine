"""
MCP Response formatters.

- 책임: MCP 클라이언트(IDE, Editor, CLI)가 기대하는 엄격한 JSON-RPC 기반 응답 구조를 생성한다.
- 주의: create_text_response 및 create_error_response의 응답 구조(키 이름, 중첩 구조 등)를 임의로 바꾸면 client 호환성이 깨질 수 있으므로 절대 구조를 변경하지 않는다.
"""
import json
import traceback

def create_text_response(rid, r, hook_msg=""):
    if isinstance(r, (dict, list)):
        final_res = json.dumps(r, ensure_ascii=False, indent=2)
    else:
        final_res = str(r)
    if hook_msg:
        final_res = f"{hook_msg}\n{final_res}"
    return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": final_res}]}}

def create_error_response(rid, e):
    return {
        "jsonrpc": "2.0", 
        "id": rid, 
        "result": {
            "isError": True, 
            "content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}]
        }
    }
