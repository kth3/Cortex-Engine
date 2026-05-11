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
