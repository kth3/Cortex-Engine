import os
import pytest
from scripts.cortex.db import to_rel_path

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
