"""
Git 이력을 분석하여 파일 간의 연관성(Co-change) 및 파일 계보(Lineage)를 추적합니다.
"""
import subprocess
import os
import time

_git_root_cache = {}

def _find_git_root(workspace, file_path):
    """상위로 올라가며 .git 폴더가 있는 실제 저장소 루트를 반환 (캐시 적용)"""
    # 1. 절대 경로로 정규화
    if os.path.isabs(file_path):
        abs_path = os.path.normpath(file_path)
    else:
        abs_path = os.path.normpath(os.path.join(workspace, file_path))
    
    target_dir = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
    if target_dir in _git_root_cache:
        return _git_root_cache[target_dir]
            
    try:
        curr = target_dir
        # 상위로 올라가며 .git 검색
        while curr and curr != os.path.dirname(curr):
            if os.path.exists(os.path.join(curr, ".git")):
                _git_root_cache[target_dir] = curr
                return curr
            curr = os.path.dirname(curr)
        _git_root_cache[target_dir] = workspace
        return workspace
    except Exception:
        return workspace

def get_file_lineage(workspace, file_path):
    """
    파일의 Git 이력 요약 추출
    """
    try:
        # 실제 저장소 루트(cwd) 동적 감지
        real_root = _find_git_root(workspace, file_path)
        # 절대 파일 경로 산출
        abs_file = os.path.normpath(os.path.join(workspace, file_path))
        # 저장소 루트 기준의 순수 상대 경로 산출
        rel_path = os.path.relpath(abs_file, real_root)

        # 마지막 수정자, 수정 시간, 커밋 횟수 추출
        cmd = ["git", "log", "-n", "1", "--format=%an|%at", "--", rel_path]
        output = subprocess.check_output(cmd, cwd=real_root, stderr=subprocess.STDOUT, timeout=15).decode().strip()
        if not output: return None
        
        author, ts = output.split("|")
        
        cmd_count = ["git", "rev-list", "--count", "HEAD", "--", rel_path]
        try:
            count = subprocess.check_output(cmd_count, cwd=real_root, timeout=15).decode().strip()
        except Exception: count = "0"
        
        return {
            "file_path": file_path,
            "last_author": author,
            "last_commit_ts": int(ts),
            "commit_count": int(count),
            "updated_at": int(time.time())
        }
    except Exception:
        return None

def get_file_history(workspace, file_path, limit=5):
    """
    파일의 최근 N개 커밋 이력 추출 (충돌 방지용)
    """
    try:
        real_root = _find_git_root(workspace, file_path)
        abs_file = os.path.normpath(os.path.join(workspace, file_path))
        rel_path = os.path.relpath(abs_file, real_root)

        # 디버깅 로그 출력 (서버 로그에서 확인 가능)
        import sys
        sys.stderr.write(f"DEBUG GIT: workspace={workspace}, real_root={real_root}, rel_path={rel_path}\\n")

        # 커밋 해시, 작성자, 시간(Unix), 메시지 추출
        cmd = ["git", "log", "-n", str(limit), "--format=%h|%an|%at|%s", "--", rel_path]
        output = subprocess.check_output(cmd, cwd=real_root, stderr=subprocess.STDOUT, timeout=15).decode().strip()
        if not output: return []
        
        history = []
        for line in output.split("\n"):
            line = line.strip()
            if not line: continue
            parts = line.split("|")
            if len(parts) >= 4:
                history.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "timestamp": int(parts[2]),
                    "message": parts[3]
                })
        return history
    except Exception:
        return []

def analyze_co_changes(workspace, limit_days=30):
    """
    최근 N일간 함께 변경된 파일들의 커플링 점수 계산 (간이 구현)
    """
    # 실제 구현은 git log --name-only 등을 파싱하여 
    # 동일 커밋에 포함된 파일 쌍의 빈도를 계산해야 함.
    # 3단계 고도화 시 상세 구현 예정.
    return []

def install_git_hooks(workspace):
    """
    post-checkout, post-merge 훅을 설치하여 자동 인덱싱 유도
    """
    hook_dir = os.path.join(workspace, ".git", "hooks")
    if not os.path.exists(hook_dir): return False
    
    scripts = {
        "post-checkout": "#!/bin/sh\npython3 .agents/scripts/cortex/indexer.py . &\n",
        "post-merge": "#!/bin/sh\npython3 .agents/scripts/cortex/indexer.py . &\n"
    }
    
    for name, content in scripts.items():
        path = os.path.join(hook_dir, name)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
    return True
