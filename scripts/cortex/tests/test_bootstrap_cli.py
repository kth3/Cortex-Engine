"""Tests for cortex-ctl bootstrap sub-command."""
from __future__ import annotations

import io
import json
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

from cortex.runtime import bootstrap_cli, control


def _run(argv, codex_home=None, claude_home=None, workspace=None):
    stdout = io.StringIO()
    targets = []
    if codex_home is not None:
        targets.append(patch.object(bootstrap_cli.codex_hook, "_codex_home", return_value=Path(codex_home)))
    if claude_home is not None:
        targets.append(patch.object(bootstrap_cli.claude_hook, "_claude_home", return_value=Path(claude_home)))
    if workspace is not None:
        targets.append(patch.object(bootstrap_cli, "resolve_workspace", return_value=Path(workspace)))

    for p in targets:
        p.start()
    try:
        with redirect_stdout(stdout):
            exit_code = bootstrap_cli.main(argv)
    finally:
        for p in targets:
            p.stop()
    return exit_code, stdout.getvalue()


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_installs_both_hook_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"
            workspace = tmp_path / "ws"
            workspace.mkdir()
            (workspace / ".git").mkdir()

            exit_code, stdout = _run(
                [],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            self.assertEqual(exit_code, 0)
            data = json.loads(stdout)
            self.assertEqual(data["action"], "bootstrap")
            self.assertEqual(data["workspace"], str(workspace))
            self.assertIn("workspace_data_dir", data)
            self.assertEqual(data["codex"]["events"], ["SessionStart"])
            self.assertEqual(data["claude"]["events"], ["SessionStart"])
            self.assertIn("cortex-codex-hook", data["codex"]["hookCommand"])
            self.assertIn("cortex-claude-hook", data["claude"]["hookCommand"])
            self.assertTrue((codex_home / "hooks.json").is_file())
            self.assertTrue((claude_home / "settings.json").is_file())

    def test_include_all_propagates_to_both_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"
            workspace = tmp_path / "ws"
            workspace.mkdir()

            _exit, stdout = _run(
                ["--include-all"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            expected_events = ["SessionStart", "UserPromptSubmit", "Stop", "PreToolUse", "PostToolUse"]
            self.assertEqual(data["codex"]["events"], expected_events)
            self.assertEqual(data["claude"]["events"], expected_events)

    def test_skip_flags_omit_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"
            workspace = tmp_path / "ws"
            workspace.mkdir()

            _exit, stdout = _run(
                ["--skip-codex"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )
            data = json.loads(stdout)
            self.assertNotIn("codex", data)
            self.assertIn("claude", data)

            _exit, stdout = _run(
                ["--skip-claude"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )
            data = json.loads(stdout)
            self.assertIn("codex", data)
            self.assertNotIn("claude", data)

    def test_dry_run_does_not_write_settings_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"
            workspace = tmp_path / "ws"
            workspace.mkdir()

            _exit, stdout = _run(
                ["--dry-run"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertTrue(data["dryRun"])
            self.assertTrue(data["codex"]["dryRun"])
            self.assertTrue(data["claude"]["dryRun"])
            self.assertFalse((codex_home / "hooks.json").exists())
            self.assertFalse((claude_home / "settings.json").exists())

    def test_enable_knowledge_expands_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            knowledge_dir = workspace / ".cortex" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            with zipfile.ZipFile(knowledge_dir / "knowledge.zip", "w") as zf:
                zf.writestr("resources/r1.md", "hi")
                zf.writestr("skills/s1.md", "skill")

            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--enable-knowledge"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["knowledge"]["action"], "enable")
            self.assertEqual(data["knowledge"]["status"], "ok")
            self.assertTrue((knowledge_dir / "resources" / "r1.md").is_file())
            self.assertTrue((knowledge_dir / "skills" / "s1.md").is_file())

    def test_dry_run_skips_knowledge_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            knowledge_dir = workspace / ".cortex" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            with zipfile.ZipFile(knowledge_dir / "knowledge.zip", "w") as zf:
                zf.writestr("resources/r1.md", "hi")

            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--enable-knowledge", "--dry-run"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["knowledge"]["status"], "dry-run-skip")
            self.assertFalse((knowledge_dir / "resources").exists())


class BootstrapDispatchTests(unittest.TestCase):
    def test_bootstrap_routes_to_bootstrap_cli(self):
        with patch.object(bootstrap_cli, "main", return_value=0) as cli_main:
            exit_code = control.main(["bootstrap", "--dry-run"])
        self.assertEqual(exit_code, 0)
        cli_main.assert_called_once_with(["--dry-run"])

    def test_help_lists_bootstrap(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            control.main(["--help"])
        self.assertIn("bootstrap", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
