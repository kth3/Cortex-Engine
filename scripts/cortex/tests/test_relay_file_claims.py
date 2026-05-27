import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _read_board(state_file):
    return json.loads(Path(state_file).read_text(encoding="utf-8"))


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


def test_force_release_clears_unity_file_claims(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["Scenes/Main.unity"])
    relay.force_release("lane-a")

    board = json.loads(state_file.read_text(encoding="utf-8"))
    assert board["lanes"]["lane-a"]["files_to_modify"] == []


def test_zombie_eviction_clears_unity_file_claims(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["Scenes/Main.unity"])

    board = json.loads(state_file.read_text(encoding="utf-8"))
    board["lanes"]["lane-a"]["locked_at"] = "2000-01-01T00:00:00Z"
    state_file.write_text(json.dumps(board), encoding="utf-8")

    relay.acquire("agent-b", "task-b", "lane-a")

    board = json.loads(state_file.read_text(encoding="utf-8"))
    assert board["lanes"]["lane-a"]["active_agent_id"] == "agent-b"
    assert board["lanes"]["lane-a"]["files_to_modify"] == []


def test_create_contract_schema_exposes_files_to_modify():
    from cortex.mcp.registry import TOOL_CREATE_TASK_CONTRACT, list_tools

    tool = next(item for item in list_tools() if item["name"] == TOOL_CREATE_TASK_CONTRACT)

    assert "files_to_modify" in tool["inputSchema"]["properties"]


def test_create_contract_isolates_claimed_file(tmp_path, monkeypatch):
    import relay
    from cortex.mcp.context import McpContext
    from cortex.mcp.tools import orchestration

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orchestration, "_save_contract_observation", lambda *_args: None)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    created_targets = []

    def _fake_create_worktree(main_workspace, target_dir):
        assert Path(main_workspace) == workspace.resolve()
        target_path = Path(target_dir)
        target_path.mkdir(parents=True)
        created_targets.append(target_path)
        return target_path

    monkeypatch.setattr(orchestration, "create_detached_worktree", _fake_create_worktree)

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", ["scripts/relay.py"])
    relay.acquire("agent-b", "task-b", "lane-b")

    ctx = McpContext(workspace=str(workspace), session_id="test-session", scripts_dir=SCRIPTS_DIR)
    result = orchestration.call_create_contract(
        ctx,
        {
            "lane_id": "lane-b",
            "task_name": "task-b",
            "instructions": "test",
            "files_to_modify": ["Scripts\\Relay.py"],
        },
    )

    assert result["isolation"]["active"] is True
    assert created_targets
    isolated = created_targets[0]
    assert result["isolation"]["isolated_workspace"] == str(isolated)
    assert Path(result["path"]).is_relative_to(isolated)
    assert relay.isolated_workspace_for_session("test-session") == str(isolated)
    assert ctx.workspace == str(workspace)


def test_dispatcher_routes_isolated_session_without_mutating_context(tmp_path, monkeypatch):
    import relay
    from cortex.mcp import dispatcher
    from cortex.mcp.context import McpContext

    monkeypatch.setattr(relay, "STATE_FILE", str(tmp_path / "state" / "board.json"))
    main_workspace = tmp_path / "main"
    isolated = tmp_path / "isolated"
    main_workspace.mkdir()
    isolated.mkdir()

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.register_isolated_workspace(
        "lane-a",
        "session-1",
        str(main_workspace),
        str(isolated),
        ["scripts/relay.py"],
        [],
    )

    monkeypatch.setitem(
        dispatcher.TOOL_HANDLERS,
        "probe_workspace",
        lambda ctx, _args: {"workspace": ctx.workspace},
    )

    ctx = McpContext(workspace=str(main_workspace), session_id="session-1", scripts_dir=SCRIPTS_DIR)
    response = dispatcher.handle_tools_call(
        ctx,
        {"name": "probe_workspace", "arguments": {}},
        1,
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload["workspace"] == str(isolated)
    assert ctx.workspace == str(main_workspace)


def test_release_removes_clean_isolated_worktree(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))
    removed = []
    monkeypatch.setattr(relay, "_is_git_worktree_dirty", lambda _path: False)
    monkeypatch.setattr(
        relay,
        "_remove_git_worktree",
        lambda main, isolated: removed.append((main, isolated)),
    )

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.register_isolated_workspace(
        "lane-a",
        "session-1",
        str(tmp_path / "main"),
        str(tmp_path / "isolated"),
        ["scripts/relay.py"],
        [],
    )
    relay.release("agent-a", "lane-a")

    board = _read_board(state_file)
    assert removed == [(str(tmp_path / "main"), str(tmp_path / "isolated"))]
    assert board["lanes"]["lane-a"]["isolation"] is None


def test_release_preserves_dirty_isolated_worktree(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))
    monkeypatch.setattr(relay, "_is_git_worktree_dirty", lambda _path: True)
    monkeypatch.setattr(
        relay,
        "_remove_git_worktree",
        lambda *_args: pytest.fail("dirty worktree must not be removed"),
    )

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.register_isolated_workspace(
        "lane-a",
        "session-1",
        str(tmp_path / "main"),
        str(tmp_path / "isolated"),
        ["scripts/relay.py"],
        [],
    )
    relay.release("agent-a", "lane-a")

    board = _read_board(state_file)
    isolation = board["lanes"]["lane-a"]["isolation"]
    assert isolation["status"] == "preserved_dirty"
    assert isolation["cleanup_status"] == "preserved_dirty"


def test_force_release_preserves_cleanup_failure_metadata(tmp_path, monkeypatch):
    import relay

    state_file = tmp_path / "state" / "board.json"
    monkeypatch.setattr(relay, "STATE_FILE", str(state_file))
    monkeypatch.setattr(relay, "_is_git_worktree_dirty", lambda _path: False)

    def _fail_remove(_main, _isolated):
        raise RuntimeError("remove failed")

    monkeypatch.setattr(relay, "_remove_git_worktree", _fail_remove)

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.register_isolated_workspace(
        "lane-a",
        "session-1",
        str(tmp_path / "main"),
        str(tmp_path / "isolated"),
        ["scripts/relay.py"],
        [],
    )
    relay.force_release("lane-a")

    board = _read_board(state_file)
    isolation = board["lanes"]["lane-a"]["isolation"]
    assert isolation["status"] == "cleanup_failed"
    assert isolation["cleanup_status"] == "cleanup_failed"
    assert "remove failed" in isolation["cleanup_error"]


def test_unity_risk_file_claim_marks_conflict(tmp_path, monkeypatch):
    import relay

    monkeypatch.setattr(relay, "STATE_FILE", str(tmp_path / "state" / "board.json"))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify("lane-a", [".\\Scenes\\Main.UNITY"])
    relay.acquire("agent-b", "task-b", "lane-b")

    with pytest.raises(relay.FileClaimConflict) as exc_info:
        relay.claim_files_to_modify("lane-b", ["Scenes/Main.unity"])

    assert exc_info.value.conflicts == [("scenes/main.unity", "lane-a")]
    assert "scenes/main.unity [Unity-risk] held by lane 'lane-a'" in str(exc_info.value)


def test_status_marks_unity_risk_files(tmp_path, monkeypatch, capsys):
    import relay

    monkeypatch.setattr(relay, "STATE_FILE", str(tmp_path / "state" / "board.json"))

    relay.acquire("agent-a", "task-a", "lane-a")
    relay.claim_files_to_modify(
        "lane-a",
        ["ProjectSettings/ProjectSettings.asset", "scripts/relay.py"],
    )

    relay.status("lane-a")

    output = capsys.readouterr().out
    assert "projectsettings/projectsettings.asset [Unity-risk]" in output
    assert "scripts/relay.py" in output


@pytest.mark.parametrize(
    "path",
    [
        "Scenes/Main.unity",
        "Assets/Bot.prefab",
        "Assets/Data.asset",
        "Assets/Bot.cs.meta",
        "ProjectSettings/EditorBuildSettings.asset",
        "Packages/manifest.json",
        "Packages/packages-lock.json",
    ],
)
def test_unity_risk_file_patterns(path):
    import relay

    assert relay.is_unity_risk_file(path)
