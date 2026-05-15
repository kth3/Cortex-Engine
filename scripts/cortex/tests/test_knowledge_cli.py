"""Tests for cortex-ctl knowledge sub-command."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import control, knowledge_cli


def _make_workspace_with_zip(zip_contents: dict[str, str] | None = None):
    """Create a temporary workspace whose .cortex/knowledge holds a synthetic zip."""
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    (workspace / ".git").mkdir()
    cortex_home = workspace / ".cortex"
    knowledge_dir = cortex_home / "knowledge"
    knowledge_dir.mkdir(parents=True)
    zip_path = knowledge_dir / "knowledge.zip"

    contents = zip_contents or {
        "resources/r1.md": "rsource one",
        "resources/sub/r2.md": "resource two",
        "examples/e1/code.py": "print('hi')",
        "skills/s1.md": "skill one",
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        for arcname, data in contents.items():
            zf.writestr(arcname, data)

    return tmp, workspace, cortex_home, knowledge_dir, zip_path


def _run_cli(argv: list[str], workspace: Path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("cortex.runtime.knowledge_cli.resolve_workspace", return_value=workspace):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = knowledge_cli.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class KnowledgeEnableTests(unittest.TestCase):
    def test_enable_expands_zip_into_knowledge_root(self):
        tmp, workspace, _ch, knowledge_dir, _zip = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)

        exit_code, stdout, stderr = _run_cli(["enable"], workspace)

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(stderr, "")
        data = json.loads(stdout)
        self.assertEqual(data["action"], "enable")
        self.assertEqual(data["status"], "ok")
        self.assertTrue((knowledge_dir / "resources" / "r1.md").is_file())
        self.assertTrue((knowledge_dir / "resources" / "sub" / "r2.md").is_file())
        self.assertTrue((knowledge_dir / "examples" / "e1" / "code.py").is_file())
        self.assertTrue((knowledge_dir / "skills" / "s1.md").is_file())
        self.assertEqual(data["expanded"]["resources"], 2)
        self.assertEqual(data["expanded"]["examples"], 1)
        self.assertEqual(data["expanded"]["skills"], 1)

    def test_enable_without_zip_returns_error(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / ".git").mkdir()
        (workspace / ".cortex").mkdir()

        exit_code, _stdout, stderr = _run_cli(["enable"], workspace)

        self.assertEqual(exit_code, 1)
        self.assertIn("zip not found", stderr)

    def test_enable_is_idempotent_without_force(self):
        tmp, workspace, _ch, knowledge_dir, _zip = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)

        _run_cli(["enable"], workspace)
        exit_code, stdout, _stderr = _run_cli(["enable"], workspace)

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "already-expanded")
        # files still present
        self.assertTrue((knowledge_dir / "resources" / "r1.md").is_file())

    def test_enable_force_overwrites_existing(self):
        tmp, workspace, _ch, knowledge_dir, zip_path = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)

        _run_cli(["enable"], workspace)
        stale_file = knowledge_dir / "resources" / "stale.md"
        stale_file.write_text("stale", encoding="utf-8")
        self.assertTrue(stale_file.is_file())

        exit_code, stdout, _stderr = _run_cli(["enable", "--force"], workspace)

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "ok")
        self.assertFalse(stale_file.is_file())  # wiped before re-extract
        self.assertTrue((knowledge_dir / "resources" / "r1.md").is_file())


class KnowledgeDisableTests(unittest.TestCase):
    def test_disable_removes_expansion_keeps_zip(self):
        tmp, workspace, _ch, knowledge_dir, zip_path = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)
        _run_cli(["enable"], workspace)

        exit_code, stdout, _stderr = _run_cli(["disable"], workspace)

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(set(data["removed"]), {"resources", "examples", "skills"})
        self.assertFalse((knowledge_dir / "resources").exists())
        self.assertFalse((knowledge_dir / "examples").exists())
        self.assertFalse((knowledge_dir / "skills").exists())
        self.assertTrue(zip_path.is_file())  # zip preserved

    def test_disable_when_nothing_expanded_is_noop(self):
        tmp, workspace, _ch, _kd, _zip = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)

        exit_code, stdout, _stderr = _run_cli(["disable"], workspace)

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "noop")
        self.assertEqual(data["removed"], [])


class KnowledgeStatusTests(unittest.TestCase):
    def test_status_before_enable_shows_zip_only(self):
        tmp, workspace, _ch, _kd, zip_path = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)

        exit_code, stdout, _stderr = _run_cli(["status"], workspace)

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertTrue(data["zip_present"])
        self.assertEqual(data["zip_path"], str(zip_path))
        self.assertEqual(data["expanded"], {})

    def test_status_after_enable_reports_counts(self):
        tmp, workspace, _ch, _kd, _zip = _make_workspace_with_zip()
        self.addCleanup(tmp.cleanup)
        _run_cli(["enable"], workspace)

        _exit, stdout, _stderr = _run_cli(["status"], workspace)
        data = json.loads(stdout)
        self.assertEqual(data["expanded"]["resources"], 2)
        self.assertEqual(data["expanded"]["examples"], 1)
        self.assertEqual(data["expanded"]["skills"], 1)

    def test_status_when_zip_missing_reports_absent(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / ".git").mkdir()
        (workspace / ".cortex").mkdir()

        _exit, stdout, _stderr = _run_cli(["status"], workspace)
        data = json.loads(stdout)
        self.assertFalse(data["zip_present"])
        self.assertIsNone(data["zip_path"])


class CortexCtlDispatchTests(unittest.TestCase):
    def test_knowledge_sub_command_routes_to_knowledge_cli(self):
        with patch.object(knowledge_cli, "main", return_value=0) as cli_main:
            exit_code = control.main(["knowledge", "status"])

        self.assertEqual(exit_code, 0)
        cli_main.assert_called_once_with(["status"])

    def test_unknown_command_returns_error(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = control.main(["nope"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Unknown command", stdout.getvalue())

    def test_help_lists_knowledge(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            control.main(["--help"])
        self.assertIn("knowledge", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
