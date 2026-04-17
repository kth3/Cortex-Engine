#!/usr/bin/env python3
"""
hooks_manager.py - 런타임 생명주기 훅 관리자
이벤트에 등록된 스크립트들을 안전하게 실행하고 결과를 반환합니다.
"""
import os
import subprocess
import sys

def dispatch(workspace, event_name, *args, **kwargs):
    """
    이벤트 이름에 해당하는 훅 스크립트가 hooks/ 폴더에 있으면 실행합니다.
    예: after_edit -> hooks/after_edit.py 실행
    """
    hooks_dir = os.path.join(workspace, ".agents", "hooks")
    hook_script = os.path.join(hooks_dir, f"{event_name}.py")
    
    if not os.path.exists(hook_script):
        return None

    try:
        # 훅 스크립트 실행 (인자 전달)
        # kwargs는 환경 변수나 별도 인자로 전달할 수 있도록 확장 가능
        str_args = [str(a) for a in args]
        cmd = [sys.executable, hook_script] + str_args
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if res.returncode == 0:
            return res.stdout.strip()
        else:
            sys.stderr.write(f"[HOOK ERROR] {event_name}: {res.stderr.strip()}\n")
            return f"Error: {res.stderr.strip()}"
            
    except Exception as e:
        sys.stderr.write(f"[DISPATCH ERROR] {event_name}: {str(e)}\n")
        return f"Exception: {str(e)}"
