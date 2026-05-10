from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class McpContext:
    workspace: str
    session_id: str
    scripts_dir: Path
