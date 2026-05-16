import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def test_file_claim_blocks_active_lane_overlap(tmp_path, monkeypatch):
    import relay

    monkeypatch.setattr(relay, "STATE_FILE", str(tmp_path / "state" / "board.json"))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["Scripts\\Relay.py", "README.md"])
    relay.acquire("agent-b", "task-b", "lane-b")

    with pytest.raises(relay.FileClaimConflict) as exc_info:
        relay.claim_files_to_modify("lane-b", ["scripts/relay.py"])

    assert exc_info.value.conflicts == [("scripts/relay.py", "lane-a")]


def test_release_clears_file_claims(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["scripts/relay.py"])
    relay.release("agent-a", "lane-a")

    board = json.loads(state_file.read_text(encoding="utf-8"))
    assert board["lanes"]["lane-a"]["files_to_modify"] == []


def test_create_contract_schema_exposes_files_to_modify():
    from cortex.mcp.registry import TOOL_PC_CREATE_CONTRACT, list_tools

    tool = next(item for item in list_tools() if item["name"] == TOOL_PC_CREATE_CONTRACT)

    assert "files_to_modify" in tool["inputSchema"]["properties"]


def test_create_contract_blocks_claimed_file(tmp_path, monkeypatch):
    import relay
    from cortex.mcp.tools import orchestration

    monkeypatch.setattr(relay, "STATE_FILE", str(tmp_path / "state" / "board.json"))
    monkeypatch.setattr(orchestration, "_save_contract_observation", lambda *_args: None)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["scripts/relay.py"])

    ctx = SimpleNamespace(workspace=str(workspace), session_id="test-session")
    with pytest.raises(relay.FileClaimConflict):
        orchestration.call_create_contract(
            ctx,
            {
                "lane_id": "lane-b",
                "task_name": "task-b",
                "instructions": "test",
                "files_to_modify": ["Scripts\\Relay.py"],
            },
        )
