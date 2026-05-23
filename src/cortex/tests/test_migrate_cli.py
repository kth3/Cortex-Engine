"""Tests for cortex-ctl migrate sub-command."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import control, migrate_cli


def _legacy_workspace_with_data():
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    (workspace / ".git").mkdir()
    cortex_dir = workspace / ".cortex"
    (cortex_dir / "data").mkdir(parents=True)
    (cortex_dir / "history").mkdir(parents=True)
    (cortex_dir / "data" / "memories.db").write_bytes(b"legacy-memdb-bytes")
    graph = cortex_dir / "data" / "graph_db_store"
    graph.mkdir()
    (graph / "shard0").write_bytes(b"graph-shard")
    (cortex_dir / "history" / "session.log").write_text("legacy session", encoding="utf-8")
    return tmp, workspace, cortex_dir


def _run(argv, env_override=None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    patches = []
    if env_override:
        for k, v in env_override.items():
            patches.append(patch.dict("os.environ", {k: v}))
    for p in patches:
        p.start()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = migrate_cli.main(argv)
    finally:
        for p in patches:
            p.stop()
    return exit_code, stdout.getvalue(), stderr.getvalue()


class MigrateRunTests(unittest.TestCase):
    def test_migrate_moves_legacy_items_to_workspace_data_dir(self):
        tmp, workspace, cortex_dir = _legacy_workspace_with_data()
        self.addCleanup(tmp.cleanup)

        exit_code, stdout, stderr = _run(["--source", str(workspace)])

        self.assertEqual(exit_code, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(set(data["moved"]), {"memories.db", "graph_db_store", "history"})

        target = Path(data["target"])
        self.assertTrue((target / "memories.db").is_file())
        self.assertTrue((target / "graph_db_store" / "shard0").is_file())
        self.assertTrue((target / "history" / "session.log").is_file())

        # legacy locations gone
        self.assertFalse((cortex_dir / "data" / "memories.db").exists())
        self.assertFalse((cortex_dir / "data" / "graph_db_store").exists())
        self.assertFalse((cortex_dir / "history").exists())
        # empty legacy data dir cleaned
        self.assertFalse((cortex_dir / "data").exists())

    def test_dry_run_does_not_touch_files(self):
        tmp, workspace, cortex_dir = _legacy_workspace_with_data()
        self.addCleanup(tmp.cleanup)

        exit_code, stdout, _stderr = _run(["--source", str(workspace), "--dry-run"])

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "dry-run")
        self.assertEqual(len(data["plan"]), 3)
        # legacy files still in place
        self.assertTrue((cortex_dir / "data" / "memories.db").is_file())
        self.assertTrue((cortex_dir / "history").is_dir())

    def test_noop_when_no_legacy_data(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / ".git").mkdir()
        (workspace / ".cortex").mkdir()

        exit_code, stdout, _stderr = _run(["--source", str(workspace)])

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "noop")

    def test_no_cortex_dir_is_handled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / ".git").mkdir()  # workspace itself, no .cortex

        exit_code, stdout, _stderr = _run(["--source", str(workspace)])

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "no-cortex-dir")

    def test_conflict_requires_force(self):
        tmp, workspace, cortex_dir = _legacy_workspace_with_data()
        self.addCleanup(tmp.cleanup)

        # First migration succeeds
        _run(["--source", str(workspace)])

        # Recreate legacy data to simulate a stale source
        (cortex_dir / "data").mkdir(parents=True, exist_ok=True)
        (cortex_dir / "data" / "memories.db").write_bytes(b"new-legacy")

        exit_code, _stdout, stderr = _run(["--source", str(workspace)])

        self.assertEqual(exit_code, 1)
        err = json.loads(stderr)
        self.assertEqual(err["status"], "conflict")
        self.assertIn("memories.db", err["conflicts"])

    def test_force_overwrites_existing_target(self):
        tmp, workspace, cortex_dir = _legacy_workspace_with_data()
        self.addCleanup(tmp.cleanup)

        _run(["--source", str(workspace)])

        (cortex_dir / "data").mkdir(parents=True, exist_ok=True)
        (cortex_dir / "data" / "memories.db").write_bytes(b"newer-legacy")

        exit_code, stdout, _stderr = _run(["--source", str(workspace), "--force"])

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "ok")
        self.assertIn("memories.db", data["moved"])
        target = Path(data["target"])
        self.assertEqual((target / "memories.db").read_bytes(), b"newer-legacy")


class CortexCtlMigrateDispatchTests(unittest.TestCase):
    def test_migrate_sub_command_routes_to_migrate_cli(self):
        with patch.object(migrate_cli, "main", return_value=0) as cli_main:
            exit_code = control.main(["migrate", "--dry-run"])

        self.assertEqual(exit_code, 0)
        cli_main.assert_called_once_with(["--dry-run"])

    def test_help_lists_migrate(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            control.main(["--help"])
        self.assertIn("migrate", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
