"""
MCP Response formatters.

- 책임: MCP 클라이언트(IDE, Editor, CLI)가 기대하는 엄격한 JSON-RPC 기반 응답 구조를 생성한다.
- 주의: create_text_response 및 create_error_response의 응답 구조(키 이름, 중첩 구조 등)를 임의로 바꾸면 client 호환성이 깨질 수 있으므로 절대 구조를 변경하지 않는다.
"""
import json
import traceback

JSONRPC_VERSION = "2.0"
CONTENT_TYPE_TEXT = "text"
ERROR_PREFIX = "Error:"
HOOK_SEPARATOR = "\n"
JSON_INDENT = 2


def _jsonrpc_result(rid, result):
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": rid,
        "result": result,
    }


def _text_content(text):
    return {
        "type": CONTENT_TYPE_TEXT,
        "text": text,
    }


def _content_result(text):
    return {
        "content": [_text_content(text)],
    }


def _error_result(text):
    return {
        "isError": True,
        "content": [_text_content(text)],
    }


def _stringify_result(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=JSON_INDENT)
    return str(value)


def _apply_hook_message(text, hook_msg):
    if hook_msg:
        return f"{hook_msg}{HOOK_SEPARATOR}{text}"
    return text


def _format_exception_text(exc):
    return f"{ERROR_PREFIX} {str(exc)}{HOOK_SEPARATOR}{traceback.format_exc()}"


def create_text_response(rid, r, hook_msg=""):
    final_res = _stringify_result(r)
    final_res = _apply_hook_message(final_res, hook_msg)
    return _jsonrpc_result(rid, _content_result(final_res))


def create_error_response(rid, e):
    return _jsonrpc_result(rid, _error_result(_format_exception_text(e)))
