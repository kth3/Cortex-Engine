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
from cortex.config.settings import load_settings
from cortex.indexing.file_pipeline import index_file
from cortex.indexing.index_roots import add_index_root, source_path_for_index_path
from cortex.storage.graph import get_graph_db_path
from cortex.indexing.scanner import scan_files
from cortex.paths import data_dir, resolve_cortex_home, settings_paths


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
    def test_db_paths_follow_workspace_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            expected_db = (data_dir(ws) / "memories.db").resolve()
            expected_graph = (data_dir(ws) / "graph_db_store").resolve()
            self.assertEqual(Path(db.get_db_path(str(ws))).resolve(), expected_db)
            self.assertEqual(Path(get_graph_db_path(str(ws))).resolve(), expected_graph)

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

            self.assertIn("src/keep.py", files)
            self.assertNotIn("big/skip.py", files)

    def test_empty_index_roots_keeps_only_forced_cortex_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".cortex" / "src" / "cortex").mkdir(parents=True)
            (ws / "src").mkdir()
            (ws / "src" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            (ws / ".cortex" / "src" / "cortex" / "keep.py").write_text("# keep\n", encoding="utf-8")
            settings = {"indexing_rules": {"index_roots": [], "include_paths": ["**"]}}

            with cortex_home_env(ws / ".cortex"):
                files = scan_files(str(ws), SUPPORTED, settings_override=settings)

            self.assertIn(".cortex/src/cortex/keep.py", files)
            self.assertNotIn("src/skip.py", files)

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

    def test_add_internal_root_uses_workspace_relative_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "Example_Project").mkdir()
            roots, entry, index_root = add_index_root(str(ws), {"indexing_rules": {"index_roots": ["."]}}, ws / "Example_Project")

            self.assertEqual(entry, "Example_Project")
            self.assertEqual(index_root.db_root, "Example_Project")
            self.assertFalse(index_root.external)
            self.assertIn("Example_Project", roots)

    def test_external_root_scans_with_alias_namespace(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ext_tmp:
            ws = Path(tmp)
            ext = Path(ext_tmp) / "Example_Project"
            (ws / ".cortex" / "scripts").mkdir(parents=True)
            (ext / "Assets").mkdir(parents=True)
            (ext / "Assets" / "Player.cs").write_text("class Player {}\n", encoding="utf-8")
            settings = {
                "indexing_rules": {
                    "index_roots": [{"path": str(ext), "alias": "Example_Project", "external": True}],
                    "include_paths": ["**"],
                }
            }

            with cortex_home_env(ws / ".cortex"):
                files = scan_files(str(ws), {".cs": ("c_sharp", lambda *_: None)}, settings_override=settings)
                source_path = source_path_for_index_path(str(ws), "@external/Example_Project/Assets/Player.cs", settings)

            self.assertIn("@external/Example_Project/Assets/Player.cs", files)
            self.assertEqual(source_path.resolve(), (ext / "Assets" / "Player.cs").resolve())

    def test_external_root_alias_collision_blocks_add(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ext_a, tempfile.TemporaryDirectory() as ext_b:
            ws = Path(tmp)
            first = Path(ext_a) / "A"
            second = Path(ext_b) / "B"
            first.mkdir()
            second.mkdir()
            settings = {"indexing_rules": {"index_roots": [{"path": str(first), "alias": "Example_Project", "external": True}]}}

            with self.assertRaises(ValueError):
                add_index_root(str(ws), settings, second, alias="Example_Project")

    def test_index_file_reads_external_source_but_stores_synthetic_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ext_tmp:
            ws = Path(tmp)
            ext = Path(ext_tmp) / "Example_Project"
            (ws / ".cortex").mkdir()
            (ext / "Docs").mkdir(parents=True)
            (ext / "Docs" / "note.md").write_text("# External Note\n\nbody\n", encoding="utf-8")
            (ws / ".cortex" / "settings.local.yaml").write_text(
                "indexing_rules:\n"
                "  index_roots:\n"
                f"    - path: {str(ext).replace(chr(92), '/')}\n"
                "      alias: Example_Project\n"
                "      external: true\n"
                "  include_paths:\n"
                "    - '**'\n",
                encoding="utf-8",
            )

            with cortex_home_env(ws / ".cortex"):
                conn = db.get_connection(str(ws))
                db.init_schema(conn)
                result = index_file(str(ws), "@external/Example_Project/Docs/note.md", conn=conn, vectorize=False)
                row = conn.execute(
                    "SELECT file_path FROM file_cache WHERE file_path=?",
                    ("@external/Example_Project/Docs/note.md",),
                ).fetchone()
                conn.close()

            self.assertNotIn("error", result)
            self.assertEqual(row[0], "@external/Example_Project/Docs/note.md")


def run():
    suite = unittest.TestLoader().loadTestsFromNames([
        f"{__name__}.PathResolverTests",
        f"{__name__}.IndexRootsTests",
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())

