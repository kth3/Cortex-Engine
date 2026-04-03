import pytest
import os
import sys

from scripts.cortex.skeleton import get_parser_internal

def test_get_parser_internal_java():
    """Test that a Java file path returns the correct java parser function."""
    parser = get_parser_internal("Test.java")
    assert parser is not None
    assert parser.__name__ == "parse_java_file"

def test_get_parser_internal_python():
    """Test that a Python file path returns the correct python parser function."""
    parser = get_parser_internal("test.py")
    assert parser is not None
    assert parser.__name__ == "parse_python_file"

def test_get_parser_internal_typescript():
    """Test that Typescript/Javascript file paths return the correct parser function."""
    for ext in [".ts", ".js", ".tsx", ".jsx"]:
        parser = get_parser_internal(f"test{ext}")
        assert parser is not None
        assert parser.__name__ == "parse_typescript_file"

def test_get_parser_internal_markdown():
    """Test that Markdown file paths return the correct parser function."""
    parser = get_parser_internal("README.md")
    assert parser is not None
    assert parser.__name__ == "parse_markdown_file"

def test_get_parser_internal_unknown_extension():
    """Test that an unknown extension returns None."""
    parser = get_parser_internal("test.unknown")
    assert parser is None

def test_get_parser_internal_no_extension():
    """Test that a file with no extension returns None."""
    parser = get_parser_internal("Makefile")
    assert parser is None

def test_get_parser_internal_import_error(monkeypatch):
    """Test that ImportError is handled gracefully for all parsers."""
    # Simulate ImportError when importing parser modules
    monkeypatch.setitem(sys.modules, "cortex.parsers.java_parser", None)
    monkeypatch.setitem(sys.modules, "cortex.parsers.python_parser", None)
    monkeypatch.setitem(sys.modules, "cortex.parsers.typescript_parser", None)
    monkeypatch.setitem(sys.modules, "cortex.parsers.markdown_parser", None)

    assert get_parser_internal("Test.java") is None
    assert get_parser_internal("test.py") is None
    assert get_parser_internal("test.ts") is None
    assert get_parser_internal("test.md") is None

def test_get_parser_internal_case_insensitive():
    """Test that file extensions are handled in a case-insensitive manner."""
    parser = get_parser_internal("test.PY")
    assert parser is not None
    assert parser.__name__ == "parse_python_file"

    parser = get_parser_internal("test.TsX")
    assert parser is not None
    assert parser.__name__ == "parse_typescript_file"
