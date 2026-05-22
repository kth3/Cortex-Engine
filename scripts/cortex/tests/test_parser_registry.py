import json
import importlib
from types import SimpleNamespace

from cortex.parsers.registry import ParserRegistry

registry_module = importlib.import_module("cortex.parsers.registry")


def test_python_parser_registry_uses_rust_binary(monkeypatch, tmp_path):
    binary = tmp_path / "cortex-watcher"
    binary.touch()

    calls = {}

    monkeypatch.setattr(registry_module, "ensure_rust_watcher_binary", lambda: binary)

    def fake_run(args, capture_output, text, check, cwd):
        calls["args"] = args
        calls["cwd"] = cwd
        return SimpleNamespace(stdout=json.dumps({"nodes": [], "edges": []}))

    monkeypatch.setattr(registry_module.subprocess, "run", fake_run)

    registry = ParserRegistry()
    language, parser = registry.get_parser(".py")

    assert language == "python"
    assert parser("src/app.py", "print('hi')\n") == {"nodes": [], "edges": []}
    assert calls["args"][1:4] == ["parse-file", "--rel", "src/app.py"]
    assert calls["cwd"] is None
