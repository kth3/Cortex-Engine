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


class BootstrapHfTokenTests(unittest.TestCase):
    def test_hf_token_arg_writes_to_data_home_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--hf-token", "hf_secret_xyz"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["hf_token"]["status"], "saved")
            env_path = Path(data["hf_token"]["path"])
            self.assertTrue(env_path.is_file())
            self.assertIn("HF_TOKEN=hf_secret_xyz", env_path.read_text(encoding="utf-8"))

    def test_hf_token_upsert_preserves_other_env_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            from cortex.paths import data_home
            existing = data_home() / ".env"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text("CORTEX_DEBUG=1\nHF_TOKEN=old\n", encoding="utf-8")

            _run(
                ["--hf-token", "new_token"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            content = existing.read_text(encoding="utf-8")
            self.assertIn("CORTEX_DEBUG=1", content)
            self.assertIn("HF_TOKEN=new_token", content)
            self.assertNotIn("HF_TOKEN=old", content)

    def test_dry_run_skips_hf_token_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--hf-token", "secret", "--dry-run"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["hf_token"]["status"], "dry-run-skip")


class BootstrapWarmModelsTests(unittest.TestCase):
    def test_warm_models_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--warm-models", "--dry-run"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["warm_models"]["status"], "dry-run-skip")

    def test_warm_models_invokes_snapshot_download(self):
        from cortex.runtime import bootstrap_cli as bcli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            with patch.object(bcli, "_warm_models", return_value={"status": "ok", "model": "Qwen/Qwen3-Embedding-0.6B"}) as warm:
                _exit, stdout = _run(
                    ["--warm-models"],
                    codex_home=codex_home,
                    claude_home=claude_home,
                    workspace=workspace,
                )

            data = json.loads(stdout)
            self.assertEqual(data["warm_models"]["status"], "ok")
            self.assertIn("Qwen", data["warm_models"]["model"])
            warm.assert_called_once()


class BootstrapEmbeddingConfigTests(unittest.TestCase):
    def test_embedding_model_arg_writes_to_data_home_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--embedding-model", "google/embeddinggemma-300m"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["embedding"]["status"], "saved")
            self.assertEqual(data["embedding"]["saved"]["model"], "google/embeddinggemma-300m")
            self.assertIn("warning", data["embedding"])
            env_path = Path(data["embedding"]["path"])
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m", content)

    def test_embedding_max_seq_length_arg_writes_to_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--embedding-max-seq-length", "2048"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["embedding"]["saved"]["max_seq_length"], 2048)
            self.assertNotIn("warning", data["embedding"])
            env_path = Path(data["embedding"]["path"])
            self.assertIn(
                "CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048",
                env_path.read_text(encoding="utf-8"),
            )

    def test_dry_run_skips_embedding_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            _exit, stdout = _run(
                ["--embedding-model", "google/embeddinggemma-300m", "--dry-run"],
                codex_home=codex_home,
                claude_home=claude_home,
                workspace=workspace,
            )

            data = json.loads(stdout)
            self.assertEqual(data["embedding"]["status"], "dry-run-skip")

    def test_warm_models_uses_embedding_model_arg(self):
        from cortex.runtime import bootstrap_cli as bcli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            codex_home = tmp_path / "codex"
            claude_home = tmp_path / "claude"

            with patch.object(bcli, "_warm_models", return_value={"status": "ok", "model": "google/embeddinggemma-300m"}) as warm:
                _exit, _stdout = _run(
                    ["--warm-models", "--embedding-model", "google/embeddinggemma-300m"],
                    codex_home=codex_home,
                    claude_home=claude_home,
                    workspace=workspace,
                )

            call_kwargs = warm.call_args.kwargs
            self.assertEqual(call_kwargs["model_id"], "google/embeddinggemma-300m")


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
