"""Unit tests for cortex.runtime.control orchestration behaviors."""
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, call, patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import control


def _lock_cm(acquired: bool):
    @contextmanager
    def _cm():
        yield acquired

    return _cm()


class StopTests(unittest.TestCase):
    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.control_lock")
    def test_stop_skips_when_lock_not_acquired(self, mock_control_lock, mock_perform_stop):
        mock_control_lock.return_value = _lock_cm(False)
        logger = Mock()

        with patch.object(control, "logger", logger):
            control.stop()

        mock_perform_stop.assert_not_called()
        logger.info.assert_any_call("Another control process is running. Skipping stop.")

    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.control_lock")
    def test_stop_calls_perform_stop_when_lock_acquired(self, mock_control_lock, mock_perform_stop):
        mock_control_lock.return_value = _lock_cm(True)
        control.stop()
        mock_perform_stop.assert_called_once()


class PerformStopTests(unittest.TestCase):
    @patch("cortex.runtime.control.os.getpid", return_value=9999)
    @patch("cortex.runtime.control.force_cleanup_ports")
    @patch("cortex.runtime.control.cleanup_ports")
    @patch("cortex.runtime.control.time.sleep")
    @patch("cortex.runtime.control.terminate_pid")
    @patch("cortex.runtime.control._request_graceful_stop", return_value=True)
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control._service_scripts")
    def test_perform_stop_with_pids_runs_full_cleanup_path(
        self,
        mock_service_scripts,
        mock_get_pids,
        mock_request_stop,
        mock_terminate_pid,
        mock_sleep,
        mock_cleanup_ports,
        mock_force_cleanup_ports,
        _mock_getpid,
    ):
        logger = Mock()
        mock_service_scripts.return_value = [
            (Path("/a/server.py"), "Engine Server"),
            (Path("/a/watch/daemon.py"), "Watcher"),
        ]
        mock_get_pids.side_effect = [[101, 102], [201]]

        with patch.object(control, "logger", logger):
            control._perform_stop()

        self.assertEqual(mock_request_stop.call_count, 3)
        mock_request_stop.assert_has_calls([call(101), call(102), call(201)], any_order=False)
        mock_terminate_pid.assert_has_calls(
            [call(101, logger), call(102, logger), call(201, logger)],
            any_order=False,
        )
        mock_sleep.assert_called_once_with(2)
        mock_cleanup_ports.assert_called_once_with(logger, 9999)
        mock_force_cleanup_ports.assert_called_once_with(logger, 9999)

    @patch("cortex.runtime.control.os.getpid", return_value=9999)
    @patch("cortex.runtime.control.force_cleanup_ports")
    @patch("cortex.runtime.control.cleanup_ports")
    @patch("cortex.runtime.control.time.sleep")
    @patch("cortex.runtime.control.terminate_pid")
    @patch("cortex.runtime.control.os.kill")
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control._service_scripts")
    def test_perform_stop_without_pids_still_forces_port_cleanup(
        self,
        mock_service_scripts,
        mock_get_pids,
        mock_os_kill,
        mock_terminate_pid,
        mock_sleep,
        mock_cleanup_ports,
        mock_force_cleanup_ports,
        _mock_getpid,
    ):
        logger = Mock()
        mock_service_scripts.return_value = [(Path("/a/server.py"), "Engine Server")]
        mock_get_pids.return_value = []

        with patch.object(control, "logger", logger):
            control._perform_stop()

        mock_os_kill.assert_not_called()
        mock_terminate_pid.assert_not_called()
        mock_sleep.assert_not_called()
        mock_cleanup_ports.assert_not_called()
        mock_force_cleanup_ports.assert_called_once_with(logger, 9999)


class StartTests(unittest.TestCase):
    @patch("cortex.runtime.control.control_lock")
    @patch("cortex.runtime.control.resolve_local_daemon_script")
    @patch("cortex.runtime.control.send_minimal_ping")
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control._is_local_daemon_running")
    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.launch_background_process")
    def test_start_returns_early_when_all_running(
        self,
        mock_launch_background_process,
        mock_perform_stop,
        mock_is_local_daemon_running,
        mock_get_pids,
        mock_send_minimal_ping,
        mock_resolve_local_daemon_script,
        mock_control_lock,
    ):
        mock_control_lock.return_value = _lock_cm(True)
        mock_get_pids.side_effect = [[10], [20]]
        mock_send_minimal_ping.return_value = True
        mock_resolve_local_daemon_script.return_value = Path("/a/daemon.py")
        mock_is_local_daemon_running.return_value = True

        log_dir = Mock()
        with patch("cortex.runtime.control.LOG_DIR", log_dir):
            control.start()

        mock_perform_stop.assert_not_called()
        mock_launch_background_process.assert_not_called()

    @patch("cortex.runtime.control.control_lock")
    @patch("cortex.runtime.control.resolve_local_daemon_script")
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control.send_minimal_ping")
    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.build_child_env", return_value={"A": "B"})
    @patch("cortex.runtime.control.launch_background_process")
    @patch("cortex.runtime.control.time.sleep")
    def test_start_returns_error_when_server_exits_immediately(
        self,
        mock_sleep,
        mock_launch_background_process,
        _mock_build_child_env,
        mock_perform_stop,
        mock_send_minimal_ping,
        mock_get_pids,
        mock_resolve_local_daemon_script,
        mock_control_lock,
    ):
        mock_control_lock.return_value = _lock_cm(True)
        mock_get_pids.side_effect = [[], []]
        mock_send_minimal_ping.return_value = False
        mock_resolve_local_daemon_script.return_value = None
        server_proc = Mock()
        server_proc.poll.return_value = 1
        server_proc.returncode = 1
        mock_launch_background_process.return_value = server_proc
        logger = Mock()

        log_dir = Mock()
        with patch.object(control, "logger", logger), patch("cortex.runtime.control.LOG_DIR", log_dir):
            control.start()

        mock_perform_stop.assert_called_once()
        logger.error.assert_called()
        self.assertTrue(
            any("CRITICAL: Engine Server exited immediately" in str(args[0]) for args, _ in logger.error.call_args_list)
        )
        mock_sleep.assert_called_once_with(5)

    @patch("cortex.runtime.control.control_lock")
    @patch("cortex.runtime.control.resolve_local_daemon_script")
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control.send_minimal_ping")
    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.build_child_env", return_value={"A": "B"})
    @patch("cortex.runtime.control.launch_background_process")
    @patch("cortex.runtime.control._launch_local_daemon")
    @patch("cortex.runtime.control.time.sleep")
    def test_start_success_path_launches_local_daemon(
        self,
        mock_sleep,
        mock_launch_local_daemon,
        mock_launch_background_process,
        _mock_build_child_env,
        mock_perform_stop,
        mock_send_minimal_ping,
        mock_get_pids,
        mock_resolve_local_daemon_script,
        mock_control_lock,
    ):
        mock_control_lock.return_value = _lock_cm(True)
        mock_get_pids.side_effect = [[], []]
        mock_send_minimal_ping.side_effect = [False, True]
        local_daemon = Path("/a/daemon.py")
        mock_resolve_local_daemon_script.return_value = local_daemon
        server_proc = Mock()
        server_proc.poll.return_value = None
        mock_launch_background_process.return_value = server_proc
        logger = Mock()

        log_dir = Mock()
        with patch.object(control, "logger", logger), patch("cortex.runtime.control.LOG_DIR", log_dir):
            control.start()

        mock_perform_stop.assert_called_once()
        mock_launch_local_daemon.assert_called_once_with(local_daemon, {"A": "B"})
        self.assertTrue(
            any("Cortex services started successfully." in str(args[0]) for args, _ in logger.info.call_args_list)
        )
        self.assertGreaterEqual(mock_sleep.call_count, 1)

    @patch("cortex.runtime.control.control_lock")
    @patch("cortex.runtime.control.resolve_local_daemon_script")
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control.send_minimal_ping")
    @patch("cortex.runtime.control._perform_stop")
    @patch("cortex.runtime.control.build_child_env", return_value={"A": "B"})
    @patch("cortex.runtime.control.launch_background_process")
    @patch("cortex.runtime.control._launch_local_daemon")
    @patch("cortex.runtime.control.time.sleep")
    def test_start_logs_error_when_readiness_fails(
        self,
        _mock_sleep,
        mock_launch_local_daemon,
        mock_launch_background_process,
        _mock_build_child_env,
        mock_perform_stop,
        mock_send_minimal_ping,
        mock_get_pids,
        mock_resolve_local_daemon_script,
        mock_control_lock,
    ):
        mock_control_lock.return_value = _lock_cm(True)
        mock_get_pids.side_effect = [[], []]
        # all_running check + 35 retries all false
        mock_send_minimal_ping.side_effect = [False] + [False] * 35
        mock_resolve_local_daemon_script.return_value = None
        server_proc = Mock()
        server_proc.poll.return_value = None
        mock_launch_background_process.return_value = server_proc
        logger = Mock()

        log_dir = Mock()
        with patch.object(control, "logger", logger), patch("cortex.runtime.control.LOG_DIR", log_dir):
            control.start()

        mock_perform_stop.assert_called_once()
        mock_launch_local_daemon.assert_not_called()
        self.assertTrue(
            any("CRITICAL: Engine Server failed to start" in str(args[0]) for args, _ in logger.error.call_args_list)
        )


class StatusTests(unittest.TestCase):
    @patch("cortex.runtime.control.print")
    @patch("cortex.runtime.control.resolve_local_daemon_script", return_value=None)
    @patch("cortex.runtime.control.get_pids")
    @patch("cortex.runtime.control.send_minimal_ping_status")
    def test_status_label_mapping(
        self,
        mock_ping_status,
        mock_get_pids,
        _mock_resolve_local_daemon,
        mock_print,
    ):
        mock_get_pids.return_value = [1]

        for ping_status, expected_label in [
            ("ok", "[READY]"),
            ("loading", "[LOADING]"),
            ("error", "[ERROR]"),
            ("weird", "[UNREACHABLE]"),
        ]:
            mock_ping_status.return_value = ping_status
            mock_print.reset_mock()

            control.status()

            printed = "\n".join(str(call_args.args[0]) for call_args in mock_print.call_args_list if call_args.args)
            self.assertIn(expected_label, printed)
            self.assertIn("Engine Server", printed)
            self.assertIn("Watcher Daemon", printed)
            self.assertIn("IPC Endpoint", printed)


class RestartTests(unittest.TestCase):
    @patch("cortex.runtime.control.start")
    @patch("cortex.runtime.control.stop")
    def test_restart_calls_stop_then_start(self, mock_stop, mock_start):
        call_order = []
        mock_stop.side_effect = lambda: call_order.append("stop")
        mock_start.side_effect = lambda: call_order.append("start")
        control.restart()
        self.assertEqual(call_order, ["stop", "start"])
        mock_stop.assert_called_once()
        mock_start.assert_called_once()


def run():
    suite = unittest.TestLoader().loadTestsFromNames(
        [
            f"{__name__}.StopTests",
            f"{__name__}.PerformStopTests",
            f"{__name__}.StartTests",
            f"{__name__}.StatusTests",
            f"{__name__}.RestartTests",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
