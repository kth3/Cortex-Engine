"""Tests for global Codex hook installation and runtime adapters."""
import io
import json
import os
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

from cortex.integrations import codex_hook, codex_session_start


def _temp_workspace():
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    (workspace / ".git").mkdir()
    cortex_home = workspace / ".cortex"
    cortex_home.mkdir()
    (cortex_home / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return tmp, workspace, cortex_home


class CodexHookRunTests(unittest.TestCase):
    def _run_main(self, argv, payload):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = codex_hook.main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_session_start_payload_cwd_resolves_workspace_and_cortex_home(self):
        tmp, workspace, cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_auto_context", return_value={"context": "prior work"}) as auto_context:
            exit_code, stdout, stderr = self._run_main(
                ["run", "SessionStart"],
                {"cwd": str(workspace), "session_id": "s1"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = json.loads(stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Cortex auto context:\nprior work", data["hookSpecificOutput"]["additionalContext"])
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.workspace, str(workspace))
        self.assertEqual(ctx.session_id, "s1")
        self.assertEqual(ctx.scripts_dir, cortex_home / "scripts")

    def test_user_prompt_submit_uses_capsule_without_auto_chain(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_capsule", return_value={"capsule": "semantic context"}) as capsule:
            exit_code, stdout, stderr = self._run_main(
                ["run", "UserPromptSubmit", "--token-budget", "321"],
                {"cwd": str(workspace), "session_id": "s1", "prompt": "rank this code"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = json.loads(stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Cortex prompt context:\nsemantic context", data["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(
            capsule.call_args.args[1],
            {"query": "rank this code", "token_budget": 321, "auto_chain": False},
        )

    def test_missing_cortex_home_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code, stdout, stderr = self._run_main(
                ["run", "SessionStart"],
                {"cwd": tmp, "session_id": "s1"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        self.assertIn(".cortex not found", stderr)

    def test_invalid_stdin_json_is_non_fatal(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sys.stdin", io.StringIO("{not-json")):
            with patch.object(codex_hook, "call_pc_auto_context", return_value={"context": ""}):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = codex_hook.main(["run", "SessionStart"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {})
        self.assertIn("invalid JSON", stderr.getvalue())


class CodexHookInstallTests(unittest.TestCase):
    def _run_install(self, codex_home, *extra):
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = ["install", "--codex-home", str(codex_home), *extra]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = codex_hook.main(argv)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_dry_run_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            exit_code, result, stderr = self._run_install(codex_home, "--dry-run")

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(result["dryRun"])
            self.assertEqual(result["events"], ["SessionStart"])
            self.assertFalse((codex_home / "hooks" / "cortex_codex_hook.py").exists())
            self.assertFalse((codex_home / "hooks.json").exists())

    def test_install_writes_launcher_and_preserves_existing_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            existing = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "echo stop"}
                            ]
                        }
                    ]
                }
            }
            (codex_home / "hooks.json").write_text(json.dumps(existing), encoding="utf-8")

            exit_code, result, stderr = self._run_install(codex_home)

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            launcher = codex_home / "hooks" / "cortex_codex_hook.py"
            self.assertTrue(launcher.exists())
            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertIn("Stop", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])
            command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn("cortex_codex_hook.py", command)
            self.assertIn("SessionStart", command)
            self.assertEqual(result["launcher"], str(launcher.resolve()))

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            self._run_install(codex_home)
            self._run_install(codex_home)

            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            handlers = [
                handler
                for group in data["hooks"]["SessionStart"]
                for handler in group["hooks"]
                if "cortex_codex_hook.py" in handler["command"]
            ]
            self.assertEqual(len(handlers), 1)

    def test_user_prompt_submit_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _exit_code, result, _stderr = self._run_install(
                codex_home,
                "--include-user-prompt-submit",
            )

            self.assertEqual(result["events"], ["SessionStart", "UserPromptSubmit"])
            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertIn("UserPromptSubmit", data["hooks"])
            command = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            self.assertIn("UserPromptSubmit", command)


class CodexSessionStartCompatibilityTests(unittest.TestCase):
    def test_compat_entrypoint_delegates_to_session_start_runner(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        payload = {"cwd": "C:/tmp/work", "session_id": "s1"}
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with patch.object(codex_session_start, "run_event", return_value={"ok": True}) as run_event:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = codex_session_start.main(["--workspace", "C:/tmp/work"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(run_event.call_args.args[0], "SessionStart")

    def test_example_uses_global_launcher_shape(self):
        example_path = THIS_DIR.parent / "integrations" / "codex_hooks.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))

        command_hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(command_hook["type"], "command")
        self.assertIn("cortex_codex_hook.py", command_hook["command"])
        self.assertIn("SessionStart", command_hook["command"])


if __name__ == "__main__":
    unittest.main()
