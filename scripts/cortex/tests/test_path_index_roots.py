"""Path resolver and index_roots regression tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import db
from cortex.graph_db import get_graph_db_path
from cortex.indexer_utils import load_settings, scan_files
from cortex.paths import resolve_cortex_home, settings_paths


SUPPORTED = {".py": ("python", lambda *_: None), ".md": ("markdown", lambda *_: None)}


class PathResolverTests(unittest.TestCase):
    def test_db_paths_preserve_agents_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            self.assertEqual(
                db.get_db_path(str(ws)),
                str(ws / ".agents" / "data" / "memories.db"),
            )
            self.assertEqual(
                db.get_db_path(str(ws / ".agents")),
                str(ws / ".agents" / "data" / "memories.db"),
            )
            self.assertEqual(
                get_graph_db_path(str(ws)),
                str(ws / ".agents" / "data" / "graph_db_store"),
            )

    def test_settings_paths_follow_cortex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            home = resolve_cortex_home(ws / ".agents" / "scripts")
            settings, local = settings_paths(ws)
            self.assertEqual(home, ws / ".agents")
            self.assertEqual(settings, ws / ".agents" / "settings.yaml")
            self.assertEqual(local, ws / ".agents" / "settings.local.yaml")


class IndexRootsTests(unittest.TestCase):
    def test_scan_files_walks_only_index_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".agents" / "scripts").mkdir(parents=True)
            (ws / "src").mkdir()
            (ws / "big").mkdir()
            (ws / "src" / "keep.py").write_text("print('keep')\n", encoding="utf-8")
            (ws / "big" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            settings = {"indexing_rules": {"index_roots": ["src"], "include_paths": ["**"]}}

            files = scan_files(str(ws), SUPPORTED, settings_override=settings)

            self.assertIn(os.path.join("src", "keep.py"), files)
            self.assertNotIn(os.path.join("big", "skip.py"), files)

    def test_empty_index_roots_keeps_only_forced_cortex_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".agents" / "scripts" / "cortex").mkdir(parents=True)
            (ws / "src").mkdir()
            (ws / "src" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            (ws / ".agents" / "scripts" / "cortex" / "keep.py").write_text("# keep\n", encoding="utf-8")
            settings = {"indexing_rules": {"index_roots": [], "include_paths": ["**"]}}

            files = scan_files(str(ws), SUPPORTED, settings_override=settings)

            self.assertIn(os.path.join(".agents", "scripts", "cortex", "keep.py"), files)
            self.assertNotIn(os.path.join("src", "skip.py"), files)

    def test_local_index_roots_override_common_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".agents").mkdir()
            (ws / ".agents" / "settings.yaml").write_text(
                "indexing_rules:\n  index_roots:\n    - .\n  include_paths:\n    - '**'\n",
                encoding="utf-8",
            )
            (ws / ".agents" / "settings.local.yaml").write_text(
                "indexing_rules:\n  index_roots:\n    - src\n",
                encoding="utf-8",
            )

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
