"""Path resolver and index_roots regression tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import storage as db
from cortex.storage.graph import get_graph_db_path
from cortex.config.settings import load_settings
from cortex.scanner.finder import scan_files
from cortex.paths import resolve_cortex_home, settings_paths


SUPPORTED = {".py": ("python", lambda *_: None), ".md": ("markdown", lambda *_: None)}


@contextmanager
def cortex_home_env(path: Path | None):
    old_home = os.environ.get("CORTEX_HOME")
    if path is not None:
        os.environ["CORTEX_HOME"] = str(path)
    elif "CORTEX_HOME" in os.environ:
        os.environ.pop("CORTEX_HOME")
        
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("CORTEX_HOME", None)
        else:
            os.environ["CORTEX_HOME"] = old_home


class PathResolverTests(unittest.TestCase):
    def test_db_paths_follow_cortex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with cortex_home_env(ws / ".cortex"):
                self.assertEqual(
                    Path(db.get_db_path(str(ws))).resolve(),
                    (ws / ".cortex" / "data" / "memories.db").resolve(),
                )
                self.assertEqual(
                    Path(db.get_db_path(str(ws / ".cortex"))).resolve(),
                    (ws / ".cortex" / "data" / "memories.db").resolve(),
                )
                self.assertEqual(
                    Path(get_graph_db_path(str(ws))).resolve(),
                    (ws / ".cortex" / "data" / "graph_db_store").resolve(),
                )

    def test_resolve_cortex_home_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with cortex_home_env(ws / ".cortex"):
                home = resolve_cortex_home(ws / ".cortex" / "scripts")
                self.assertEqual(home.resolve(), (ws / ".cortex").resolve())

    def test_resolve_cortex_home_from_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with cortex_home_env(None):
                home = resolve_cortex_home(ws / ".cortex" / "scripts")
                self.assertEqual(home.resolve(), (ws / ".cortex").resolve())

    def test_settings_paths_follow_cortex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with cortex_home_env(ws / ".cortex"):
                settings, local = settings_paths(ws)
                self.assertEqual(settings.resolve(), (ws / ".cortex" / "settings.yaml").resolve())
                self.assertEqual(local.resolve(), (ws / ".cortex" / "settings.local.yaml").resolve())


class IndexRootsTests(unittest.TestCase):
    def test_scan_files_walks_only_index_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".cortex" / "scripts").mkdir(parents=True)
            (ws / "src").mkdir()
            (ws / "big").mkdir()
            (ws / "src" / "keep.py").write_text("print('keep')\n", encoding="utf-8")
            (ws / "big" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            settings = {"indexing_rules": {"index_roots": ["src"], "include_paths": ["**"]}}

            with cortex_home_env(ws / ".cortex"):
                files = scan_files(str(ws), SUPPORTED, settings_override=settings)

            self.assertIn(os.path.join("src", "keep.py"), files)
            self.assertNotIn(os.path.join("big", "skip.py"), files)

    def test_empty_index_roots_keeps_only_forced_cortex_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".cortex" / "scripts" / "cortex").mkdir(parents=True)
            (ws / "src").mkdir()
            (ws / "src" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            (ws / ".cortex" / "scripts" / "cortex" / "keep.py").write_text("# keep\n", encoding="utf-8")
            settings = {"indexing_rules": {"index_roots": [], "include_paths": ["**"]}}

            with cortex_home_env(ws / ".cortex"):
                files = scan_files(str(ws), SUPPORTED, settings_override=settings)

            self.assertIn(os.path.join(".cortex", "scripts", "cortex", "keep.py"), files)
            self.assertNotIn(os.path.join("src", "skip.py"), files)

    def test_local_index_roots_override_common_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".cortex").mkdir()
            (ws / ".cortex" / "settings.yaml").write_text(
                "indexing_rules:\n  index_roots:\n    - .\n  include_paths:\n    - '**'\n",
                encoding="utf-8",
            )
            (ws / ".cortex" / "settings.local.yaml").write_text(
                "indexing_rules:\n  index_roots:\n    - src\n",
                encoding="utf-8",
            )

            with cortex_home_env(ws / ".cortex"):
                settings = load_settings(str(ws))

            self.assertEqual(settings["indexing_rules"]["index_roots"], ["src"])


def run():
    suite = unittest.TestLoader().loadTestsFromNames([
        f"{__name__}.PathResolverTests",
        f"{__name__}.IndexRootsTests",
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
