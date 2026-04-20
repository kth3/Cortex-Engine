#!/usr/bin/env python3
"""
orchestrator.py - 멀티 에이전트 협업 및 작업 상태 관리 코어
Todo 관리, 계약(Contract) 생성 및 검증 루프 제어.
"""
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

def get_todo_path(workspace):
    return os.path.join(workspace, ".agents", "history", "todo.json")


class _FileLock:
    """크로스 플랫폼 파일 락 (Windows/Linux/macOS 호환).
    
    외부 라이브러리 없이 .lock 파일의 원자적 생성(O_CREAT|O_EXCL)을 이용.
    fcntl 없이도 멀티 프로세스 동시성을 안전하게 제어합니다.
    """
    def __init__(self, lock_path: str, timeout: float = 10.0, poll_interval: float = 0.05):
        self.lock_path = lock_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd = None

    def acquire(self):
        start = time.monotonic()
        while True:
            try:
                # O_CREAT | O_EXCL: 파일이 없을 때만 원자적으로 생성 (크로스 플랫폼)
                self._fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                # 락 소유자 정보 기록 (디버깅용)
                os.write(self._fd, f"{os.getpid()}:{time.time()}".encode())
                return
            except FileExistsError:
                # 락 파일이 이미 존재 → 다른 프로세스가 점유 중
                if time.monotonic() - start > self.timeout:
                    # 타임아웃: 좀비 락일 가능성 → 강제 해제 후 재시도
                    try:
                        os.remove(self.lock_path)
                    except OSError:
                        pass
                    continue
                time.sleep(self.poll_interval)

    def release(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            os.remove(self.lock_path)
        except OSError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def manage_todo(workspace, action, task=None, task_id=None):
    todo_file = get_todo_path(workspace)
    os.makedirs(os.path.dirname(todo_file), exist_ok=True)
    
    # 크로스 플랫폼 배타적 락 (Windows/Linux/macOS 호환)
    with _FileLock(todo_file + ".lock"):
        # 초기 파일 보장 (락 내부에서 수행하여 레이스 컨디션 방지)
        if not os.path.exists(todo_file) or os.path.getsize(todo_file) == 0:
            with open(todo_file, "w", encoding="utf-8") as f:
                json.dump({"todos": []}, f)

        with open(todo_file, "r+", encoding="utf-8") as f:
            data = json.load(f)

            if action == "add":
                # Fix #6: len+1 방식은 clear 후 ID 재사용 가능 → max(id)+1 방식으로 교체
                existing_ids = [int(t["id"]) for t in data["todos"] if str(t.get("id", "")).isdigit()]
                new_id = str(max(existing_ids) + 1) if existing_ids else "1"
                data["todos"].append({
                    "id": new_id,
                    "task": task,
                    "done": False,
                    "created_at": str(datetime.now())
                })
                res = {"success": True, "id": new_id}
            elif action == "check":
                for t in data["todos"]:
                    if t["id"] == task_id:
                        t["done"] = True
                        t["completed_at"] = str(datetime.now())
                res = {"success": True}
            elif action == "clear":
                data = {"todos": []}
                res = {"success": True}
            else:
                return data

            # 쓰기 작업이 일어난 경우 파일 포인터를 되돌리고 갱신
            f.seek(0)
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.truncate()
            
    return res

def create_contract(workspace, session_id, lane_id, task_name, instructions, files=None):
    artifacts_dir = Path(workspace) / ".agents" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    contract_filename = f"contract_{lane_id}_{timestamp}.md"
    contract_path = artifacts_dir / contract_filename
    
    content = f"""# Task Contract: {task_name}
- **Lane**: {lane_id}
- **Created**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Session**: {session_id}

## 📝 Instructions
{instructions}

## 📂 Targeted Files
{", ".join(files) if files else "Not specified"}

## ⚠️ Constraints
- MUST use 'pc_strict_replace'.
- Todo Cleared required for release.
"""
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    return {"contract_id": contract_filename, "path": str(contract_path)}
