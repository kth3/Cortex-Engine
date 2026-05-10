import json
import os
from datetime import datetime

from .lock import FileLock


def get_todo_path(workspace):
    return os.path.join(workspace, ".agents", "history", "todo.json")


def manage_todo(workspace, action, task=None, task_id=None):
    todo_file = get_todo_path(workspace)
    os.makedirs(os.path.dirname(todo_file), exist_ok=True)

    with FileLock(todo_file + ".lock"):
        if not os.path.exists(todo_file) or os.path.getsize(todo_file) == 0:
            with open(todo_file, "w", encoding="utf-8") as f:
                json.dump({"todos": []}, f)

        with open(todo_file, "r+", encoding="utf-8") as f:
            data = json.load(f)

            if action == "add":
                existing_ids = [
                    int(t["id"]) for t in data["todos"] if str(t.get("id", "")).isdigit()
                ]
                new_id = str(max(existing_ids) + 1) if existing_ids else "1"
                data["todos"].append(
                    {
                        "id": new_id,
                        "task": task,
                        "done": False,
                        "created_at": str(datetime.now()),
                    }
                )
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

            f.seek(0)
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.truncate()

    return res
