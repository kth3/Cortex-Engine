"""Unit tests for cortex.runtime.ports."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime import ports


class _BrokenConn:
    @property
    def laddr(self):
        raise RuntimeError("broken laddr")


class ConnectionPortTests(unittest.TestCase):
    def test_connection_port_from_object(self):
        conn = SimpleNamespace(laddr=SimpleNamespace(port=42384))
        self.assertEqual(ports._connection_port(conn), 42384)

    def test_connection_port_from_tuple(self):
        conn = SimpleNamespace(laddr=("127.0.0.1", 42384))
        self.assertEqual(ports._connection_port(conn), 42384)

    def test_connection_port_from_empty_laddr(self):
        self.assertIsNone(ports._connection_port(SimpleNamespace(laddr=None)))
        self.assertIsNone(ports._connection_port(SimpleNamespace(laddr=())))

    def test_connection_port_handles_exception(self):
        self.assertIsNone(ports._connection_port(_BrokenConn()))


class OccupiedTargetPortsTests(unittest.TestCase):
    @patch("cortex.runtime.ports.psutil.net_connections")
    def test_occupied_target_ports_filters_expected_connections(self, mock_net_connections):
        mock_net_connections.return_value = [
            SimpleNamespace(laddr=SimpleNamespace(port=42384), status="LISTEN", pid=1001),
            SimpleNamespace(laddr=SimpleNamespace(port=42385), status="LISTEN", pid=1002),
            SimpleNamespace(laddr=SimpleNamespace(port=42384), status="TIME_WAIT", pid=1003),
            SimpleNamespace(laddr=SimpleNamespace(port=42384), status="LISTEN", pid=None),
            SimpleNamespace(laddr=SimpleNamespace(port=42384), status="LISTEN", pid=9999),
        ]

        rows = ports.occupied_target_ports(
            target_ports=[42384],
            current_pid=9999,
            statuses={"LISTEN", "ESTABLISHED"},
        )

        self.assertEqual(rows, [(42384, 1001, "LISTEN")])

    @patch("cortex.runtime.ports.psutil.net_connections", side_effect=RuntimeError("net fail"))
    def test_occupied_target_ports_propagates_net_connections_error(self, _mock_net_connections):
        with self.assertRaises(RuntimeError):
            ports.occupied_target_ports([42384], current_pid=1, statuses={"LISTEN"})


class WaitForPortsReleaseTests(unittest.TestCase):
    @patch("cortex.runtime.ports.time.sleep")
    @patch("cortex.runtime.ports.occupied_target_ports")
    @patch("cortex.runtime.ports.time.time")
    def test_wait_for_ports_release_logs_and_sleeps_when_occupied(
        self,
        mock_time,
        mock_occupied,
        mock_sleep,
    ):
        logger = Mock()
        mock_time.side_effect = [0.0, 0.1, 0.2, 0.3]
        mock_occupied.side_effect = [
            [(42384, 1111, "LISTEN")],
            [],
        ]

        ports.wait_for_ports_release(
            logger,
            target_ports=[42384],
            current_pid=9999,
            timeout_seconds=1.0,
            poll_interval_seconds=0.5,
        )

        logger.warning.assert_called_once()
        mock_sleep.assert_called_once_with(0.5)

    @patch("cortex.runtime.ports.time.sleep")
    @patch("cortex.runtime.ports.occupied_target_ports", side_effect=RuntimeError("occupied fail"))
    @patch("cortex.runtime.ports.time.time")
    def test_wait_for_ports_release_swallows_exception_and_returns(
        self,
        mock_time,
        _mock_occupied,
        mock_sleep,
    ):
        logger = Mock()
        mock_time.side_effect = [0.0, 0.1]

        ports.wait_for_ports_release(
            logger,
            target_ports=[42384],
            current_pid=9999,
            timeout_seconds=1.0,
        )

        logger.warning.assert_not_called()
        mock_sleep.assert_not_called()

    @patch("cortex.runtime.ports.time.sleep")
    @patch("cortex.runtime.ports.occupied_target_ports", return_value=[(42384, 1111, "LISTEN")])
    @patch("cortex.runtime.ports.time.time")
    def test_wait_for_ports_release_times_out_without_raising(
        self,
        mock_time,
        _mock_occupied,
        mock_sleep,
    ):
        logger = Mock()
        mock_time.side_effect = [0.0, 1.0, 2.1]

        ports.wait_for_ports_release(
            logger,
            target_ports=[42384],
            current_pid=9999,
            timeout_seconds=2.0,
            poll_interval_seconds=0.25,
        )

        logger.warning.assert_called()
        mock_sleep.assert_called()


class ForceReleasePortsTests(unittest.TestCase):
    @patch("cortex.runtime.ports.psutil.Process")
    @patch("cortex.runtime.ports.occupied_target_ports")
    def test_force_release_ports_kill_and_wait_called(self, mock_occupied, mock_process_ctor):
        logger = Mock()
        p1 = Mock()
        p2 = Mock()
        mock_process_ctor.side_effect = [p1, p2]
        mock_occupied.return_value = [
            (42384, 1111, "LISTEN"),
            (42385, 2222, "ESTABLISHED"),
        ]

        ports.force_release_ports(
            logger,
            target_ports=[42384, 42385],
            current_pid=9999,
        )

        self.assertEqual(mock_process_ctor.call_count, 2)
        p1.kill.assert_called_once()
        p2.kill.assert_called_once()
        p1.wait.assert_called_once_with(timeout=3.0)
        p2.wait.assert_called_once_with(timeout=3.0)

    @patch("cortex.runtime.ports.psutil.Process")
    @patch("cortex.runtime.ports.occupied_target_ports")
    def test_force_release_ports_uses_custom_wait_timeout(self, mock_occupied, mock_process_ctor):
        logger = Mock()
        proc = Mock()
        mock_process_ctor.return_value = proc
        mock_occupied.return_value = [(42384, 1111, "LISTEN")]

        ports.force_release_ports(
            logger,
            target_ports=[42384],
            current_pid=9999,
            kill_wait_seconds=7.5,
        )

        proc.wait.assert_called_once_with(timeout=7.5)

    @patch("cortex.runtime.ports.psutil.Process", side_effect=ports.psutil.NoSuchProcess(pid=1111))
    @patch("cortex.runtime.ports.occupied_target_ports", return_value=[(42384, 1111, "LISTEN")])
    def test_force_release_ports_ignores_no_such_process(
        self,
        _mock_occupied,
        _mock_process_ctor,
    ):
        logger = Mock()
        ports.force_release_ports(logger, target_ports=[42384], current_pid=9999)
        logger.debug.assert_not_called()

    @patch("cortex.runtime.ports.psutil.Process", side_effect=ports.psutil.AccessDenied(pid=1111))
    @patch("cortex.runtime.ports.occupied_target_ports", return_value=[(42384, 1111, "LISTEN")])
    def test_force_release_ports_ignores_access_denied(
        self,
        _mock_occupied,
        _mock_process_ctor,
    ):
        logger = Mock()
        ports.force_release_ports(logger, target_ports=[42384], current_pid=9999)
        logger.debug.assert_not_called()

    @patch("cortex.runtime.ports.occupied_target_ports", side_effect=RuntimeError("outer fail"))
    def test_force_release_ports_logs_debug_on_outer_exception(self, _mock_occupied):
        logger = Mock()
        ports.force_release_ports(logger, target_ports=[42384], current_pid=9999)
        logger.debug.assert_called_once()


def run():
    suite = unittest.TestLoader().loadTestsFromNames(
        [
            f"{__name__}.ConnectionPortTests",
            f"{__name__}.OccupiedTargetPortsTests",
            f"{__name__}.WaitForPortsReleaseTests",
            f"{__name__}.ForceReleasePortsTests",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
