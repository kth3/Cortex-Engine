"""Tests for CORTEX_START_TIMEOUT env override and LOADING-aware readiness."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import control


class TestResolveStartTimeout:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_START_TIMEOUT", raising=False)
        assert control._resolve_start_timeout() == control.DEFAULT_ENGINE_READY_MAX_RETRIES

    def test_env_override_with_positive_int(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "120")
        assert control._resolve_start_timeout() == 120

    def test_non_integer_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "abc")
        assert control._resolve_start_timeout() == control.DEFAULT_ENGINE_READY_MAX_RETRIES

    def test_zero_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "0")
        assert control._resolve_start_timeout() == control.DEFAULT_ENGINE_READY_MAX_RETRIES

    def test_negative_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "-10")
        assert control._resolve_start_timeout() == control.DEFAULT_ENGINE_READY_MAX_RETRIES

    def test_blank_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "   ")
        assert control._resolve_start_timeout() == control.DEFAULT_ENGINE_READY_MAX_RETRIES


class TestWaitForEngineReadyStatusBranches:
    def _server_proc(self, returncode=None):
        proc = Mock()
        proc.poll.return_value = returncode
        return proc

    def test_immediate_ok_returns_true_without_polling_long(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "5")
        with patch.object(control, "send_minimal_ping_status", return_value="ok"), \
             patch.object(control.time, "sleep") as mock_sleep:
            result = control._wait_for_engine_ready(self._server_proc())
        assert result is True
        mock_sleep.assert_not_called()

    def test_persistent_loading_succeeds_with_info_log(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "3")
        logger = Mock()
        with patch.object(control, "logger", logger), \
             patch.object(control, "send_minimal_ping_status", return_value="loading"), \
             patch.object(control.time, "sleep"):
            result = control._wait_for_engine_ready(self._server_proc())
        assert result is True
        info_msgs = " ".join(str(c.args[0]) for c in logger.info.call_args_list)
        assert "still loading in background" in info_msgs

    def test_persistent_unreachable_fails_with_critical(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "3")
        logger = Mock()
        with patch.object(control, "logger", logger), \
             patch.object(control, "send_minimal_ping_status", return_value="unreachable"), \
             patch.object(control.time, "sleep"):
            result = control._wait_for_engine_ready(self._server_proc())
        assert result is False
        err_msgs = " ".join(str(c.args[0]) for c in logger.error.call_args_list)
        assert "failed to become ready" in err_msgs

    def test_server_crash_returns_false(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "10")
        logger = Mock()
        with patch.object(control, "logger", logger), \
             patch.object(control, "send_minimal_ping_status", return_value="unreachable"), \
             patch.object(control.time, "sleep"):
            result = control._wait_for_engine_ready(self._server_proc(returncode=1))
        assert result is False
        err_msgs = " ".join(str(c.args[0]) for c in logger.error.call_args_list)
        assert "crashed during startup" in err_msgs

    def test_loading_then_ok_returns_true_before_deadline(self, monkeypatch):
        monkeypatch.setenv("CORTEX_START_TIMEOUT", "10")
        with patch.object(
            control,
            "send_minimal_ping_status",
            side_effect=["loading", "loading", "ok"],
        ), patch.object(control.time, "sleep"):
            result = control._wait_for_engine_ready(self._server_proc())
        assert result is True
