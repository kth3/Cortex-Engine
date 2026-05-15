"""Tests for the Codex SessionStart auto context adapter."""
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

from cortex.integrations import codex_session_start


class CodexSessionStartTests(unittest.TestCase):
    def _run(self, argv=None, env=None, cwd=None, result=None, side_effect=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = env or {}
        result = result if result is not None else {"context": "", "totalChars": 0, "itemCount": 0}

        with patch.dict(os.environ, env, clear=True):
            with patch("os.getcwd", return_value=str(cwd or Path.cwd())):
                with patch.object(codex_session_start, "call_pc_auto_context", side_effect=side_effect, return_value=result) as auto_context:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        exit_code = codex_session_start.main(argv or [])

        return exit_code, stdout.getvalue(), stderr.getvalue(), auto_context

    def test_explicit_workspace_wins_over_environment(self):
        with tempfile.TemporaryDirectory() as explicit, tempfile.TemporaryDirectory() as env_workspace:
            exit_code, _stdout, _stderr, auto_context = self._run(
                ["--workspace", explicit],
                env={"CORTEX_WORKSPACE": env_workspace},
            )

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.workspace, str(Path(explicit).resolve()))

    def test_environment_workspace_wins_over_cwd(self):
        with tempfile.TemporaryDirectory() as env_workspace, tempfile.TemporaryDirectory() as cwd:
            exit_code, _stdout, _stderr, auto_context = self._run(
                env={"CORTEX_WORKSPACE": env_workspace},
                cwd=cwd,
            )

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.workspace, str(Path(env_workspace).resolve()))

    def test_cwd_is_default_workspace(self):
        with tempfile.TemporaryDirectory() as cwd:
            exit_code, _stdout, _stderr, auto_context = self._run(cwd=cwd)

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.workspace, str(Path(cwd).resolve()))

    def test_codex_session_id_wins_over_cortex_session_id(self):
        exit_code, _stdout, _stderr, auto_context = self._run(
            env={"CODEX_SESSION_ID": "codex-1", "CORTEX_SESSION_ID": "cortex-1"}
        )

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.session_id, "codex-1")

    def test_cortex_session_id_is_used_when_codex_session_id_is_missing(self):
        exit_code, _stdout, _stderr, auto_context = self._run(env={"CORTEX_SESSION_ID": "cortex-1"})

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.session_id, "cortex-1")

    def test_default_session_id_is_used_when_env_is_missing(self):
        exit_code, _stdout, _stderr, auto_context = self._run()

        self.assertEqual(exit_code, 0)
        ctx = auto_context.call_args.args[0]
        self.assertEqual(ctx.session_id, codex_session_start.DEFAULT_SESSION_ID)

    def test_prints_context_when_context_exists(self):
        exit_code, stdout, stderr, auto_context = self._run(
            ["--token-budget", "123"],
            result={"context": "line 1\nline 2", "totalChars": 13, "itemCount": 1},
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "Cortex auto context:\nline 1\nline 2\n")
        self.assertEqual(stderr, "")
        self.assertEqual(auto_context.call_args.args[1], {"token_budget": 123})

    def test_prints_empty_context_message_by_default(self):
        exit_code, stdout, stderr, _auto_context = self._run()

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "Cortex auto context: (empty)\n")
        self.assertEqual(stderr, "")

    def test_quiet_empty_suppresses_empty_context_message(self):
        exit_code, stdout, stderr, _auto_context = self._run(["--quiet-empty"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_auto_context_error_is_non_fatal(self):
        exit_code, stdout, stderr, _auto_context = self._run(side_effect=RuntimeError("db offline"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "[Cortex auto context unavailable: db offline]\n")


class CodexHookExampleTests(unittest.TestCase):
    def test_example_uses_codex_hooks_json_session_start_shape(self):
        example_path = THIS_DIR.parent / "integrations" / "codex_hooks.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))

        session_start = data["hooks"]["SessionStart"]
        self.assertIsInstance(session_start, list)
        self.assertGreater(len(session_start), 0)

        command_hook = session_start[0]["hooks"][0]
        self.assertEqual(command_hook["type"], "command")
        self.assertIn("cortex-codex-session-start", command_hook["command"])


if __name__ == "__main__":
    unittest.main()
