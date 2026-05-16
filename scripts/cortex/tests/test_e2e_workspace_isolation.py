"""End-to-end checks for multi-workspace isolation and migrate round-trip."""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex import paths
from cortex.runtime import migrate_cli


class WorkspaceIsolationTests(unittest.TestCase):
    def test_distinct_repos_get_distinct_data_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = base / "repo-a"
            b = base / "repo-b"
            a.mkdir()
            b.mkdir()

            data_a = paths.workspace_data_dir(a)
            data_b = paths.workspace_data_dir(b)

            self.assertNotEqual(data_a, data_b)
            self.assertTrue(data_a.is_dir())
            self.assertTrue(data_b.is_dir())

    def test_workspace_key_env_groups_repos_into_same_data_dir(self, ):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = base / "repo-a"
            b = base / "repo-b"
            a.mkdir()
            b.mkdir()

            with patch.dict("os.environ", {"CORTEX_WORKSPACE_KEY": "monorepo"}):
                data_a = paths.workspace_data_dir(a)
                data_b = paths.workspace_data_dir(b)

            self.assertEqual(data_a, data_b)

    def test_data_dir_resolves_through_data_home_override(self):
        with tempfile.TemporaryDirectory() as override:
            with patch.dict("os.environ", {"CORTEX_DATA_HOME": override}):
                ws = Path(override) / "tmp-ws"
                data = paths.workspace_data_dir(ws)
                self.assertTrue(str(data).startswith(str(Path(override).resolve())))


class MigrateRoundTripTests(unittest.TestCase):
    def _run_migrate(self, argv):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = migrate_cli.main(argv)
        return exit_code, stdout.getvalue()

    def test_legacy_db_round_trips_into_global_workspace_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "proj"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            cortex_dir = workspace / ".cortex"
            (cortex_dir / "data").mkdir(parents=True)
            (cortex_dir / "history").mkdir()
            (cortex_dir / "data" / "memories.db").write_bytes(b"legacy-memdb-v1")
            (cortex_dir / "data" / "graph_db_store").mkdir()
            (cortex_dir / "data" / "graph_db_store" / "shard").write_bytes(b"g")
            (cortex_dir / "history" / "session.log").write_text("ok", encoding="utf-8")

            exit_code, stdout = self._run_migrate(["--source", str(workspace)])
            self.assertEqual(exit_code, 0)
            result = json.loads(stdout)
            self.assertEqual(result["status"], "ok")

            target = paths.workspace_data_dir(workspace)
            self.assertEqual((target / "memories.db").read_bytes(), b"legacy-memdb-v1")
            self.assertTrue((target / "graph_db_store" / "shard").is_file())
            self.assertEqual(
                (target / "history" / "session.log").read_text(encoding="utf-8"), "ok"
            )

            # legacy files cleaned
            self.assertFalse((cortex_dir / "data" / "memories.db").exists())
            self.assertFalse((cortex_dir / "history").exists())

            # second run is a no-op (no legacy data left)
            exit_code, stdout = self._run_migrate(["--source", str(workspace)])
            self.assertEqual(exit_code, 0)
            result = json.loads(stdout)
            self.assertEqual(result["status"], "noop")


class KnowledgeBootstrapE2ETests(unittest.TestCase):
    def test_bootstrap_with_enable_knowledge_writes_extracted_files(self):
        from cortex.runtime import bootstrap_cli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            knowledge_dir = workspace / ".cortex" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            with zipfile.ZipFile(knowledge_dir / "knowledge.zip", "w") as zf:
                zf.writestr("resources/a.md", "hi")
                zf.writestr("examples/e/code.py", "print(1)")

            codex_home = tmp_path / "codex-home"
            claude_home = tmp_path / "claude-home"

            with patch.object(bootstrap_cli, "resolve_workspace", return_value=workspace), \
                 patch.object(bootstrap_cli.codex_hook, "_codex_home", return_value=codex_home), \
                 patch.object(bootstrap_cli.claude_hook, "_claude_home", return_value=claude_home):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = bootstrap_cli.main(["--enable-knowledge", "--include-all"])

            self.assertEqual(exit_code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["knowledge"]["status"], "ok")
            self.assertTrue((knowledge_dir / "resources" / "a.md").is_file())
            self.assertTrue((knowledge_dir / "examples" / "e" / "code.py").is_file())
            # both hook adapters touched their files
            self.assertTrue((codex_home / "hooks.json").is_file())
            self.assertTrue((claude_home / "settings.json").is_file())


if __name__ == "__main__":
    unittest.main()
