from __future__ import annotations

import socket
import threading
import unittest

from tests import support  # noqa: F401
from daemon_protocol import (
    FrameSendError,
    FrameTooLarge,
    classify_send_failure,
    recv_frame,
    send_frame,
)
from request_registry import RequestRegistry
from ssh_daemon import SSHDaemon
from ssh_execute import DaemonAttempt, daemon_attempt_to_result, may_fallback


class FailingSocket:
    def __init__(self, fail_after: int):
        self.fail_after = fail_after
        self.sent = 0

    def send(self, data):
        if self.sent >= self.fail_after:
            raise ConnectionResetError("reset")
        count = min(len(data), self.fail_after - self.sent)
        self.sent += count
        return count


class DaemonProtocolTests(unittest.TestCase):
    def test_socketpair_round_trip_preserves_unicode(self):
        sender, receiver = socket.socketpair()
        try:
            message = {"request_id": "req-1", "command": "echo 中文"}
            sent = send_frame(sender, message)
            self.assertGreater(sent, 4)
            self.assertEqual(message, recv_frame(receiver, timeout=1))
        finally:
            sender.close()
            receiver.close()

    def test_zero_bytes_sent_allows_fallback(self):
        attempt = classify_send_failure(FrameSendError("connect failed", bytes_sent=0), "req-1")
        self.assertEqual("not_sent", attempt.disposition)

    def test_any_bytes_sent_makes_outcome_unknown(self):
        with self.assertRaises(FrameSendError) as raised:
            send_frame(FailingSocket(fail_after=7), {"request_id": "req-2", "command": "apply"})
        self.assertEqual(7, raised.exception.bytes_sent)
        attempt = classify_send_failure(raised.exception, "req-2")
        self.assertEqual("outcome_unknown", attempt.disposition)
        self.assertEqual("req-2", attempt.request_id)

    def test_oversized_frame_is_rejected_before_send(self):
        target = FailingSocket(fail_after=100)
        with self.assertRaises(FrameTooLarge):
            send_frame(target, {"payload": "x" * 2_100_000})
        self.assertEqual(0, target.sent)

    def test_unknown_outcome_cannot_fall_back_to_direct_execution(self):
        attempt = DaemonAttempt("outcome_unknown", "req-3")

        self.assertFalse(may_fallback(attempt))
        result = daemon_attempt_to_result(attempt)
        self.assertEqual("outcome_unknown", result["error"]["code"])
        self.assertEqual("req-3", result["meta"]["request_id"])

    def test_duplicate_submit_executes_side_effect_once(self):
        daemon = SSHDaemon.__new__(SSHDaemon)
        daemon._requests = RequestRegistry()
        daemon._lock = threading.Lock()
        calls = []
        daemon._execute_command_unlocked = lambda command, timeout: (
            calls.append(command) or {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
        )

        class ImmediateThread:
            def __init__(self, *, target, args, daemon):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        request = {
            "protocol_version": "1.0",
            "request_id": "req-side-effect",
            "command": "increment-counter",
            "remote_timeout": 30,
        }
        first = daemon._submit_execution(request, thread_factory=ImmediateThread)
        second = daemon._submit_execution(request, thread_factory=ImmediateThread)

        self.assertEqual(["increment-counter"], calls)
        self.assertEqual("succeeded", first["request"]["state"])
        self.assertEqual("succeeded", second["request"]["state"])

    def test_daemon_bounds_result_before_caching(self):
        daemon = SSHDaemon.__new__(SSHDaemon)
        daemon._requests = RequestRegistry()
        daemon._lock = threading.Lock()
        daemon._execute_command_unlocked = lambda command, timeout: {
            "success": True,
            "exit_code": 0,
            "stdout": "x" * 300_000 + "TAIL",
            "stderr": "",
        }

        class ImmediateThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        response = daemon._submit_execution(
            {
                "protocol_version": "1.0",
                "request_id": "req-large",
                "command": "generate-output",
                "remote_timeout": 30,
            },
            thread_factory=ImmediateThread,
        )

        cached = response["request"]["result"]
        self.assertLessEqual(len(cached["stdout"].encode("utf-8")), 256 * 1024)
        self.assertTrue(cached["output"]["stdout"]["truncated"])


if __name__ == "__main__":
    unittest.main()
