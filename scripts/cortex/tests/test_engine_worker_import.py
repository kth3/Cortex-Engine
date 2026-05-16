"""Regression: engine_worker must import _load_model from cortex.embeddings.provider.

The previous code had `from vector_engine import _load_model`, referencing a
non-existent module. That caused ImportError at runtime, killing model loading
without surfacing a hard error to cortex-ctl — every embedding request failed
silently. This test pins the correct import path so the regression cannot
re-emerge.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _engine_worker_source() -> str:
    path = SCRIPTS_DIR / "cortex" / "runtime" / "engine_worker.py"
    return path.read_text(encoding="utf-8")


def test_engine_worker_does_not_import_phantom_vector_engine_module():
    source = _engine_worker_source()
    assert "from vector_engine" not in source, (
        "engine_worker.py imports a non-existent 'vector_engine' module. "
        "Use 'from cortex.embeddings.provider import _load_model'."
    )


def test_engine_worker_imports_load_model_from_cortex_provider():
    source = _engine_worker_source()
    tree = ast.parse(source)
    imports_load_model = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "cortex.embeddings.provider" and any(
                alias.name == "_load_model" for alias in node.names
            ):
                imports_load_model = True
                break
    assert imports_load_model, (
        "engine_worker.py must import _load_model from cortex.embeddings.provider"
    )


def test_engine_worker_module_imports_without_error():
    """Engine worker module imports must succeed (excluding the deferred
    _load_model import inside _load_model_bg, which only runs on worker start)."""
    from cortex.runtime import engine_worker  # noqa: F401
    assert hasattr(engine_worker, "_load_model_bg")
    assert hasattr(engine_worker, "_shutdown_worker")


def test_load_model_symbol_is_resolvable_from_provider():
    """Verify the symbol _load_model_bg expects to import actually exists."""
    from cortex.embeddings.provider import _load_model
    assert callable(_load_model)
