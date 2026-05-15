from datetime import datetime
from pathlib import Path


def create_contract(workspace, session_id, lane_id, task_name, instructions, files=None):
    artifacts_dir = Path(workspace) / ".cortex" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    contract_filename = f"contract_{lane_id}_{timestamp}.md"
    contract_path = artifacts_dir / contract_filename

    content = f"""# Task Contract: {task_name}
- **Lane**: {lane_id}
- **Created**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Session**: {session_id}

## Instructions
{instructions}

## Targeted Files
{", ".join(files) if files else "Not specified"}

## Constraints
- Use strict replacement tools for file edits.
- Clear outstanding todos before release.
"""
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {"contract_id": contract_filename, "path": str(contract_path)}
