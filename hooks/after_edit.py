#!/usr/bin/env python3
"""
after_edit.py - 코드 수정 직후 자동 검증 훅
수정된 파일의 구문 오류를 체크하여 에이전트에게 즉각 피드백을 제공합니다.
"""
import sys
import subprocess
import os

def run_hook(file_path):
    """
    파일 확장자에 따라 적절한 린터/구문 검사기를 실행합니다.
    """
    if not os.path.exists(file_path):
        return None

    ext = os.path.splitext(file_path)[1]
    result = None

    try:
        if ext == ".py":
            # Python 구문 검사
            res = subprocess.run([sys.executable, "-m", "py_compile", file_path], 
                                capture_output=True, text=True)
            if res.returncode != 0:
                result = f"[LINT ERROR] Python syntax error detected:\n{res.stderr}"
        
        elif ext in [".js", ".ts", ".tsx"]:
            # Node.js 환경이 있을 경우 단순 구문 검사 (필요 시 eslint 등 확장)
            pass

    except Exception as e:
        return f"[HOOK ERROR] Failed to run validation hook: {str(e)}"

    return result

if __name__ == "__main__":
    if len(sys.argv) > 1:
        error = run_hook(sys.argv[1])
        if error:
            print(error)
