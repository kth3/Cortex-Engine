"""MCP tool handler module.

- 책임: 클라이언트로부터 전달된 MCP 요청 인자를 검증하고, 도메인 함수를 호출한 뒤 응답을 포맷팅하는 책임을 가진다.
- 주의: 외부 클라이언트와의 통신 계약을 담당하므로, tool 이름, 반환 구조, error response 형식을 임의로 변경하지 않는다.
"""
from cortex import git_analyzer as pc_git_mod

def call_pc_git_log(ctx, args):
    try:
        history = pc_git_mod.get_file_history(ctx.workspace, args["file_path"], args.get("limit", 5))
        return history
    except Exception as e:
        return {"error": str(e)}
