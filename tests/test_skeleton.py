import sys
import types
from scripts.cortex.skeleton import get_parser_internal

def test_get_parser_internal_java_success(monkeypatch):
    """Test successful resolution of java parser."""
    dummy_parser = types.ModuleType("java_parser")
    dummy_parser.parse_java_file = "dummy_func"

    monkeypatch.setitem(sys.modules, "cortex", types.ModuleType("cortex"))
    monkeypatch.setitem(sys.modules, "cortex.parsers", types.ModuleType("cortex.parsers"))
    monkeypatch.setitem(sys.modules, "cortex.parsers.java_parser", dummy_parser)

    import importlib
    monkeypatch.setattr(importlib, "reload", lambda m: m)

    result = get_parser_internal("test.java")
    assert result == "dummy_func"

def test_get_parser_internal_java_import_error(monkeypatch):
    """Test handling of missing java parser (ImportError)."""
    original_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "cortex.parsers.java_parser":
            raise ImportError("Mocked ImportError")
        return original_import(name, *args, **kwargs)

    import builtins
    monkeypatch.setattr(builtins, "__import__", mock_import)

    result = get_parser_internal("test.java")
    assert result is None

def test_get_parser_internal_unknown_ext():
    """Test handling of unsupported file extensions."""
    assert get_parser_internal("test.txt") is None
    assert get_parser_internal("test") is None
