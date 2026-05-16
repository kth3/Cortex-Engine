"""Tests for OS-correct child process isolation and graceful stop."""
from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import control, process


class TestIsolationKwargs:
    def test_isolate_false_returns_empty(self):
        assert process._isolation_kwargs(isolate=False) == {}

    def test_posix_isolation_uses_start_new_session(self):
        with patch.object(process.os, "name", "posix"):
            result = process._isolation_kwargs(isolate=True)
        assert result == {"start_new_session": True}
        assert "creationflags" not in result

    def test_windows_isolation_sets_creationflags_with_new_process_group(self):
        with patch.object(process.os, "name", "nt"):
            result = process._isolation_kwargs(isolate=True)
        assert "creationflags" in result
        assert result["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in result


class TestRequestGracefulStop:
    def test_posix_sends_terminate(self):
        fake_proc = patch.object(control.psutil, "Process").start()
        try:
            instance = fake_proc.return_value
            with patch.object(control.os, "name", "posix"):
                ok = control._request_graceful_stop(1234)
            assert ok is True
            instance.terminate.assert_called_once_with()
            instance.send_signal.assert_not_called()
        finally:
            patch.stopall()

    def test_windows_sends_ctrl_break_event(self):
        fake_proc = patch.object(control.psutil, "Process").start()
        try:
            instance = fake_proc.return_value
            with patch.object(control.os, "name", "nt"):
                ok = control._request_graceful_stop(1234)
            assert ok is True
            instance.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        finally:
            patch.stopall()

    def test_no_such_process_returns_false(self):
        with patch.object(
            control.psutil,
            "Process",
            side_effect=control.psutil.NoSuchProcess(pid=1234),
        ):
            assert control._request_graceful_stop(1234) is False

    def test_windows_falls_back_to_terminate_when_signal_blocked(self):
        fake_proc = patch.object(control.psutil, "Process").start()
        try:
            instance = fake_proc.return_value
            instance.send_signal.side_effect = OSError("not supported")
            with patch.object(control.os, "name", "nt"):
                ok = control._request_graceful_stop(1234)
            assert ok is True
            instance.terminate.assert_called_once_with()
        finally:
            patch.stopall()


class TestDaemonSignalHandlers:
    def test_signal_handlers_register_for_sigterm(self):
        from cortex.watch import daemon

        observer = type("FakeObserver", (), {"stop": lambda self: None})()

        with patch.object(daemon.signal, "signal") as mock_signal:
            daemon._install_signal_handlers(observer)

        registered_signals = {call.args[0] for call in mock_signal.call_args_list}
        assert signal.SIGTERM in registered_signals

    def test_signal_handler_calls_observer_stop(self):
        from cortex.watch import daemon

        stop_called = []

        class FakeObserver:
            def stop(self):
                stop_called.append(True)

        captured_handler = {}

        def _capture(signum, handler):
            captured_handler["fn"] = handler

        with patch.object(daemon.signal, "signal", side_effect=_capture):
            daemon._install_signal_handlers(FakeObserver())

        # Simulate a signal arriving
        captured_handler["fn"](signal.SIGTERM, None)
        assert stop_called == [True]
        assert daemon._SHUTDOWN_REQUESTED is True
        daemon._SHUTDOWN_REQUESTED = False  # reset for other tests
