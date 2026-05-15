"""Tests for global Claude Code hook installation and runtime adapters."""
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

from cortex.integrations import claude_hook


def _temp_workspace():
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    (workspace / ".git").mkdir()
    cortex_home = workspace / ".cortex"
    cortex_home.mkdir()
    (cortex_home / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return tmp, workspace, cortex_home


class ClaudeHookRunTests(unittest.TestCase):
    def _run_main(self, argv, payload):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = claude_hook.main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_session_start_payload_cwd_resolves_workspace_and_cortex_home(self):
        tmp, workspace, cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(claude_hook, "call_pc_auto_context", return_value={"context": "prior work"}) as auto_context:
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

        with patch.object(claude_hook, "call_pc_capsule", return_value={"capsule": "semantic context"}) as capsule:
            exit_code, stdout, stderr = self._run_main(
                ["run", "UserPromptSubmit", "--token-budget", "321"],
                {"cwd": str(workspace), "prompt": "rank this code"},
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

    def test_stop_extracts_last_assistant_from_transcript(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        transcript_dir = tempfile.TemporaryDirectory()
        self.addCleanup(transcript_dir.cleanup)
        transcript = Path(transcript_dir.name) / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "first reply"}],
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "second reply"}],
                        },
                    }),
                ]
            ),
            encoding="utf-8",
        )

        with patch.object(claude_hook, "call_pc_session_sync", return_value={"success": True}) as session_sync:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "Stop"],
                {"cwd": str(workspace), "transcript_path": str(transcript)},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        self.assertEqual(session_sync.call_args.args[1], {"task_desc": "second reply"})

    def test_stop_without_transcript_is_no_op(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(claude_hook, "call_pc_session_sync") as session_sync:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "Stop"],
                {"cwd": str(workspace)},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        session_sync.assert_not_called()

    def test_pre_tool_use_edit_returns_skeleton(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(claude_hook, "call_pc_skeleton", return_value="class Foo:\n  pass") as skeleton:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PreToolUse"],
                {"cwd": str(workspace), "tool_name": "Edit", "tool_input": {"file_path": "src/foo.py"}},
            )

        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertIn("Cortex skeleton for src/foo.py", data["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(skeleton.call_args.args[1], {"file_path": "src/foo.py", "detail": "standard"})

    def test_pre_tool_use_ignores_bash(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(claude_hook, "call_pc_skeleton") as skeleton:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PreToolUse"],
                {"cwd": str(workspace), "tool_name": "Bash", "tool_input": {"command": "ls"}},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        skeleton.assert_not_called()

    def test_post_tool_use_write_records_observation(self):
        tmp, workspace, _cortex_home = _temp_workspace()
        self.addCleanup(tmp.cleanup)

        with patch.object(claude_hook, "call_save_observation", return_value={"success": True}) as save_obs:
            exit_code, stdout, _stderr = self._run_main(
                ["run", "PostToolUse"],
                {
                    "cwd": str(workspace),
                    "session_id": "s-99",
                    "tool_name": "Write",
                    "tool_input": {"file_path": "src/bar.py"},
                },
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        args = save_obs.call_args.args[1]
        self.assertIn("Write edited src/bar.py", args["content"])
        self.assertIn("session=s-99", args["content"])
        self.assertEqual(args["file_paths"], ["src/bar.py"])

    def test_missing_cortex_home_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code, stdout, stderr = self._run_main(
                ["run", "SessionStart"],
                {"cwd": tmp},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), {})
        self.assertIn(".cortex not found", stderr)

    def test_invalid_stdin_json_is_non_fatal(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sys.stdin", io.StringIO("{not-json")):
            with patch.object(claude_hook, "call_pc_auto_context", return_value={"context": ""}):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = claude_hook.main(["run", "SessionStart"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {})
        self.assertIn("invalid JSON", stderr.getvalue())


class ClaudeHookInstallTests(unittest.TestCase):
    def _run_install(self, claude_home, *extra):
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = ["install", "--claude-home", str(claude_home), *extra]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = claude_hook.main(argv)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_dry_run_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            exit_code, result, stderr = self._run_install(claude_home, "--dry-run")

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(result["dryRun"])
            self.assertEqual(result["events"], ["SessionStart"])
            self.assertFalse((claude_home / "hooks" / "cortex_claude_hook.py").exists())
            self.assertFalse((claude_home / "settings.json").exists())

    def test_install_writes_launcher_and_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            existing = {
                "theme": "dark",
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]
                },
            }
            (claude_home / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

            exit_code, result, stderr = self._run_install(claude_home)

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            launcher = claude_home / "hooks" / "cortex_claude_hook.py"
            self.assertTrue(launcher.exists())
            data = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(data["theme"], "dark")
            self.assertIn("Stop", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])
            command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn("cortex_claude_hook.py", command)
            self.assertIn("SessionStart", command)
            self.assertEqual(result["launcher"], str(launcher.resolve()))

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            self._run_install(claude_home)
            self._run_install(claude_home)

            data = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
            handlers = [
                handler
                for group in data["hooks"]["SessionStart"]
                for handler in group["hooks"]
                if "cortex_claude_hook.py" in handler["command"]
            ]
            self.assertEqual(len(handlers), 1)

    def test_include_all_installs_every_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            _exit_code, result, _stderr = self._run_install(claude_home, "--include-all")

            self.assertEqual(
                result["events"],
                ["SessionStart", "UserPromptSubmit", "Stop", "PreToolUse", "PostToolUse"],
            )

    def test_pre_tool_use_install_writes_edit_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            self._run_install(claude_home, "--include-pre-tool-use")

            data = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
            group = data["hooks"]["PreToolUse"][0]
            self.assertEqual(group["matcher"], "Edit|Write|MultiEdit")
            self.assertIn("PreToolUse", group["hooks"][0]["command"])

    def test_post_tool_use_install_writes_edit_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            self._run_install(claude_home, "--include-post-tool-use")

            data = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
            group = data["hooks"]["PostToolUse"][0]
            self.assertEqual(group["matcher"], "Edit|Write|MultiEdit")
            self.assertIn("PostToolUse", group["hooks"][0]["command"])

    def test_stop_install_has_no_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp)
            self._run_install(claude_home, "--include-stop")

            data = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
            group = data["hooks"]["Stop"][0]
            self.assertNotIn("matcher", group)


class ClaudeHookLauncherSourceTests(unittest.TestCase):
    def test_launcher_uses_uv_global_cache_by_default(self):
        source = claude_hook._launcher_source()
        self.assertNotIn(".uv-cache-local", source)
        self.assertIn("CORTEX_UV_CACHE_DIR", source)
        self.assertIn("if override_cache", source)

    def test_launcher_calls_cortex_claude_hook_entry(self):
        source = claude_hook._launcher_source()
        self.assertIn("cortex-claude-hook", source)


class ClaudeHookExampleTests(unittest.TestCase):
    def test_example_uses_global_launcher_shape(self):
        example_path = THIS_DIR.parent / "integrations" / "claude_hooks.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        command_hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(command_hook["type"], "command")
        self.assertIn("cortex_claude_hook.py", command_hook["command"])
        self.assertIn("SessionStart", command_hook["command"])


if __name__ == "__main__":
    unittest.main()
