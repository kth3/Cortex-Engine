import re

path = "/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex/db.py"
with open(path, "r") as f:
    content = f.read()

# Replace get_db_path logic
old_path_logic = """# DB 파일 경로: 프로젝트 내 .agents/cortex_data/index.db
def get_db_path(workspace: str) -> str:
    # workspace가 이미 .agents를 포함하고 있다면 중복 결합 방지
    if workspace.endswith(".agents"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".agents")
        
    db_dir = os.path.join(base_dir, "cortex_data")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "index.db")"""

new_path_logic = """# DB 파일 경로: 프로젝트 내 .cortex/memories.db
def get_db_path(workspace: str) -> str:
    if workspace.endswith(".cortex"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".cortex")
        
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "memories.db")"""

content = content.replace(old_path_logic, new_path_logic)

with open(path, "w") as f:
    f.write(content)

