#!/usr/bin/env python3
"""
orchestrator.py - 멀티 에이전트 협업 및 작업 상태 관리 코어
Todo 관리, 계약(Contract) 생성 및 검증 루프 제어.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

def get_todo_path(workspace):
    return os.path.join(workspace, ".agents", "history", "todo.json")

def manage_todo(workspace, action, task=None, task_id=None):
    todo_file = get_todo_path(workspace)
    os.makedirs(os.path.dirname(todo_file), exist_ok=True)
    
    if not os.path.exists(todo_file):
        data = {"todos": []}
    else:
        with open(todo_file, "r") as f:
            data = json.load(f)

    if action == "add":
        new_id = str(len(data["todos"]) + 1)
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

    with open(todo_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
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
