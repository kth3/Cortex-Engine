import os
import sys

# Ensure scripts can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.cortex.indexer import get_module_name

def test_get_module_name_no_settings():
    """Test get_module_name when settings have no package_roots."""
    # Only one part
    assert get_module_name("file.py", {}) == "root"

    # Multiple parts
    assert get_module_name(os.path.join("src", "main.py"), {}) == "src"
    assert get_module_name(os.path.join("a", "b", "c.py"), {}) == "a"

def test_get_module_name_with_package_roots():
    """Test get_module_name when package_roots is provided."""
    settings = {
        "package_roots": ["src", "lib/modules"]
    }

    # Matches "src"
    assert get_module_name(os.path.join("src", "my_module", "file.py"), settings) == "my_module"
    assert get_module_name(os.path.join("src", "other_module", "sub", "file.py"), settings) == "other_module"

    # Matches "lib/modules"
    assert get_module_name(os.path.join("lib", "modules", "core", "file.py"), settings) == "core"

    # Does not match any package root, falls back to first part
    assert get_module_name(os.path.join("tests", "test_file.py"), settings) == "tests"

def test_get_module_name_with_package_roots_exact_match():
    """Test get_module_name when path is exactly in the package root (no module subfolder)."""
    settings = {
        "package_roots": ["src"]
    }

    # If the file is directly in the package root, the module is "root" or the file name
    # The existing logic fallback: parts = rel_path.split(os.sep); return parts[0] if len(parts) > 1 else "root"
    # Actually wait, if the file is src/file.py, root_parts is ["src"]. len(path_parts) is 2, len(root_parts) is 1.
    # So path_parts[1] is "file.py". That's what it should return.
    assert get_module_name(os.path.join("src", "file.py"), settings) == "file.py"
