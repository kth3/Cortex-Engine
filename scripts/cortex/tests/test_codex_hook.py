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

    def test_stop_payload_calls_session_sync_with_last_assistant_message(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_session_sync", return_value={"success": True}) as session_sync:
            exit_code, stdout, stderr = self._run_main(
                ["run", "Stop"],
                {"cwd": str(workspace), "session_id": "s1", "last_assistant_message": "did the thing"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {})
        self.assertEqual(session_sync.call_args.args[1], {"task_desc": "did the thing"})

    def test_stop_payload_truncates_long_assistant_message(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        long_msg = "x" * (codex_hook.MAX_STOP_TASK_DESC_CHARS + 500)
        with patch.object(codex_hook, "call_pc_session_sync", return_value={"success": True}) as session_sync:
            self._run_main(
                ["run", "Stop"],
                {"cwd": str(workspace), "session_id": "s1", "last_assistant_message": long_msg},
            )

        self.assertEqual(
            len(session_sync.call_args.args[1]["task_desc"]),
            codex_hook.MAX_STOP_TASK_DESC_CHARS,
        )

    def test_stop_without_message_is_no_op(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_session_sync") as session_sync:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "Stop"],
                {"cwd": str(workspace), "session_id": "s1"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        session_sync.assert_not_called()

    def test_pre_tool_use_apply_patch_returns_skeleton_context(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_skeleton", return_value="class Foo:\n  def bar(): ...") as skeleton:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PreToolUse"],
                {
                    "cwd": str(workspace),
                    "tool_name": "apply_patch",
                    "tool_input": {"path": "src/foo.py"},
                },
            )

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertIn("Cortex skeleton for src/foo.py", data["hookSpecificOutput"]["additionalContext"])
        self.assertIn("class Foo", data["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(skeleton.call_args.args[1], {"file_path": "src/foo.py", "detail": "standard"})

    def test_pre_tool_use_ignores_non_apply_patch_tool(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_pc_skeleton") as skeleton:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PreToolUse"],
                {"cwd": str(workspace), "tool_name": "shell", "tool_input": {"command": "ls"}},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        skeleton.assert_not_called()

    def test_post_tool_use_apply_patch_records_observation(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(codex_hook, "call_save_observation", return_value={"success": True}) as save_obs:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PostToolUse"],
                {
                    "cwd": str(workspace),
                    "tool_name": "apply_patch",
                    "tool_input": {"path": "src/foo.py"},
                    "turn_id": "t-42",
                },
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        args = save_obs.call_args.args[1]
        self.assertIn("apply_patch edited src/foo.py", args["content"])
        self.assertIn("turn=t-42", args["content"])
        self.assertEqual(args["file_paths"], ["src/foo.py"])

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
            self.assertIn("cortex-codex-hook", result["hookCommand"])
            self.assertFalse((codex_home / "hooks.json").exists())

    def test_install_writes_hooks_json_and_preserves_existing_entries(self):
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
            self.assertIn("cortex-codex-hook", result["hookCommand"])
            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertIn("Stop", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])
            command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn("cortex-codex-hook", command)
            self.assertIn("SessionStart", command)

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
                if "cortex-codex-hook" in handler["command"]
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

    def test_include_all_installs_every_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _exit_code, result, _stderr = self._run_install(codex_home, "--include-all")

            self.assertEqual(
                result["events"],
                [
                    "SessionStart",
                    "UserPromptSubmit",
                    "Stop",
                    "PreToolUse",
                    "PostToolUse",
                ],
            )

    def test_pre_tool_use_install_writes_apply_patch_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            self._run_install(codex_home, "--include-pre-tool-use")

            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            group = data["hooks"]["PreToolUse"][0]
            self.assertEqual(group["matcher"], "apply_patch")
            self.assertIn("PreToolUse", group["hooks"][0]["command"])

    def test_post_tool_use_install_writes_apply_patch_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            self._run_install(codex_home, "--include-post-tool-use")

            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            group = data["hooks"]["PostToolUse"][0]
            self.assertEqual(group["matcher"], "apply_patch")
            self.assertIn("PostToolUse", group["hooks"][0]["command"])

    def test_stop_install_has_no_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            self._run_install(codex_home, "--include-stop")

            data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            group = data["hooks"]["Stop"][0]
            self.assertNotIn("matcher", group)


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

    def test_example_uses_global_hook_entry(self):
        example_path = THIS_DIR.parent / "integrations" / "codex_hooks.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))

        command_hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(command_hook["type"], "command")
        self.assertIn("cortex-codex-hook", command_hook["command"])
        self.assertIn("SessionStart", command_hook["command"])


if __name__ == "__main__":
    unittest.main()
