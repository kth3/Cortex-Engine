import os
import pytest
from scripts.cortex.db import to_rel_path, get_db_path, to_abs_path

def test_get_db_path_normal(tmp_path):
    """
    Test when workspace does NOT end with .agents
    """
    workspace = str(tmp_path / "project")
    db_path = get_db_path(workspace)

    expected = os.path.join(workspace, ".agents", "cortex_data", "index.db")
    assert db_path == expected
    assert os.path.exists(os.path.dirname(db_path))

def test_get_db_path_ends_with_agents(tmp_path):
    """
    Test when workspace ends with .agents to prevent duplicate path joining
    """
    workspace = str(tmp_path / "project" / ".agents")
    db_path = get_db_path(workspace)

    expected = os.path.join(workspace, "cortex_data", "index.db")
    assert db_path == expected
    assert os.path.exists(os.path.dirname(db_path))

def test_get_db_path_makedirs_exception(monkeypatch):
    """
    Test that get_db_path bubbles up exceptions from os.makedirs when it fails
    """
    def mock_makedirs(*args, **kwargs):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(os, "makedirs", mock_makedirs)

    with pytest.raises(PermissionError, match="Permission denied"):
        get_db_path("/some/workspace")

def test_to_rel_path_normal():
    """
    Note: The actual implementation of to_rel_path in scripts/cortex/db.py
    differs from the simplified snippet in the issue description.
    The actual code prepends "ROOT/" and replaces backslashes to normalize paths.
    This test asserts against the *actual* behavior of the function in the codebase
    to prevent regressions, as requested by previous code reviews.
    """
    workspace = "/home/user/project"
    full_path = "/home/user/project/src/main.py"

    result = to_rel_path(full_path, workspace)
    assert result == "ROOT/src/main.py"

def test_to_rel_path_same_dir():
    workspace = "/home/user/project"
    full_path = "/home/user/project"

    result = to_rel_path(full_path, workspace)
    assert result == "ROOT/."

def test_to_rel_path_empty():
    """
    Note: The actual implementation safely handles empty paths and None values,
    returning the original path. This test covers those edge cases present in the
    real application code.
    """
    assert to_rel_path("", "/workspace") == ""
    assert to_rel_path(None, "/workspace") is None
    assert to_rel_path("/path", "") == "/path"
    assert to_rel_path("/path", None) == "/path"

def test_to_rel_path_backslash_normalization(mocker):
    # Test that backslashes are replaced by forward slashes
    workspace = "C:\\workspace"
    full_path = "C:\\workspace\\src\\main.py"

    mocker.patch("os.path.relpath", return_value="src\\main.py")

    result = to_rel_path(full_path, workspace)
    assert result == "ROOT/src/main.py"

def test_to_rel_path_exception(mocker):
    """
    Test the exception handling block. The actual code catches Exception (which
    includes ValueError) and returns the original full_path.
    """
    workspace = "C:/workspace"
    full_path = "D:/other/path"

    mocker.patch("os.path.relpath", side_effect=ValueError("path is on mount 'D:', start on mount 'C:'"))

    result = to_rel_path(full_path, workspace)

    assert result == full_path

def test_to_abs_path_normal():
    workspace = "/home/user/project"
    rel_path = "ROOT/src/main.py"

    result = to_abs_path(rel_path, workspace)

    expected = os.path.abspath(os.path.join(workspace, "src/main.py"))
    assert result == expected

def test_to_abs_path_windows_slash():
    workspace = "C:\\workspace"
    rel_path = "ROOT\\src\\main.py"

    result = to_abs_path(rel_path, workspace)

    expected = os.path.abspath(os.path.join(workspace, "src\\main.py"))
    assert result == expected

def test_to_abs_path_no_root():
    workspace = "/home/user/project"
    rel_path = "src/main.py"

    result = to_abs_path(rel_path, workspace)

    assert result == "src/main.py"

def test_to_abs_path_empty_or_none():
    workspace = "/home/user/project"

    assert to_abs_path("", workspace) == ""
    assert to_abs_path(None, workspace) is None
    assert to_abs_path("ROOT/src", "") == "ROOT/src"
    assert to_abs_path("ROOT/src", None) == "ROOT/src"
