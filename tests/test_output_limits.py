from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401
from output_limits import (
    BoundedText,
    OutputLimits,
    ProgressEmitter,
    ProgressLimiter,
    bound_result_output,
    progress_is_enabled,
)
from ssh_download import make_progress_callback as make_download_progress
from ssh_upload import make_progress_callback as make_upload_progress
from native_ssh_client import NativeSSHClient
from paramiko_client import ParamikoClient


class NonTerminal(io.StringIO):
    def isatty(self):
        return False


class OutputLimitTests(unittest.TestCase):
    def test_large_output_is_bounded_and_keeps_tail(self):
        collector = BoundedText(OutputLimits(max_bytes=32, max_lines=10, tail_bytes=12))
        collector.feed(("head\n" + "middle\n" * 10 + "TAIL-END\n").encode("utf-8"))

        result = collector.finish()

        self.assertTrue(result.truncated)
        self.assertIn("TAIL-END", result.text)
        self.assertLessEqual(len(result.text.encode("utf-8")), 32)
        self.assertGreater(result.total_bytes, 32)

    def test_unicode_is_not_split_at_byte_limit(self):
        collector = BoundedText(OutputLimits(max_bytes=7, max_lines=10, tail_bytes=4))
        collector.feed("甲乙丙丁".encode("utf-8"))

        result = collector.finish()

        self.assertNotIn("�", result.text)
        self.assertLessEqual(len(result.text.encode("utf-8")), 7)

    def test_line_limit_keeps_first_and_last_lines(self):
        result = bound_result_output(
            {"stdout": "first\nsecond\nthird\nlast\n", "stderr": ""},
            OutputLimits(max_bytes=100, max_lines=2, tail_bytes=20),
        )

        self.assertEqual("first\nlast\n", result["stdout"])
        self.assertTrue(result["output"]["stdout"]["truncated"])

    def test_progress_requires_time_and_percent_thresholds(self):
        limiter = ProgressLimiter(min_interval_seconds=1.0, min_percent_delta=1.0)
        self.assertTrue(limiter.should_emit(0, 1000, now=0.0))
        self.assertFalse(limiter.should_emit(100, 1000, now=0.2))
        self.assertFalse(limiter.should_emit(5, 1000, now=1.2))
        self.assertTrue(limiter.should_emit(100, 1000, now=1.2))
        self.assertTrue(limiter.should_emit(1000, 1000, now=1.3))

    def test_one_gibibyte_transfer_emits_at_most_twelve_events(self):
        stream = NonTerminal()
        now = [0.0]
        emitter = ProgressEmitter(stream, enabled=True, clock=lambda: now[0])
        total = 1024**3
        chunk = 128 * 1024
        speed = 100 * 1024**2
        for transferred in range(0, total + 1, chunk):
            now[0] = transferred / speed
            emitter.emit("upload", transferred, total)

        lines = stream.getvalue().splitlines()
        self.assertLessEqual(len(lines), 12)
        self.assertTrue(all(json.loads(line)["operation"] == "upload" for line in lines))

    def test_non_terminal_progress_is_off_unless_explicit(self):
        stream = NonTerminal()
        self.assertFalse(progress_is_enabled(None, stream))
        self.assertTrue(progress_is_enabled(True, stream))
        self.assertFalse(progress_is_enabled(False, stream))

    def test_transfer_entrypoints_share_bounded_progress_emitter(self):
        class Progress:
            transferred_bytes = 50
            total_bytes = 100
            file_path = "example.bin"

        stream = NonTerminal()
        self.assertIsNone(make_upload_progress(None, stream=stream))
        callback = make_download_progress(True, stream=stream, clock=lambda: 1.0)
        callback(Progress())

        event = json.loads(stream.getvalue())
        self.assertEqual("download", event["operation"])
        self.assertEqual(50, event["transferred_bytes"])

    def test_paramiko_execute_bounds_stdout_and_stderr(self):
        class Channel:
            @staticmethod
            def recv_exit_status():
                return 0

        class Stream:
            channel = Channel()

            def __init__(self, value):
                self.value = value

            def read(self):
                return self.value

        class Connection:
            @staticmethod
            def exec_command(command, timeout):
                return (
                    None,
                    Stream(b"x" * 300_000 + b"STDOUT-TAIL"),
                    Stream(b"y" * 300_000 + b"STDERR-TAIL"),
                )

        client = ParamikoClient("example.invalid", "user", key_file="example-key")
        client._get_connection = lambda: Connection()

        result = client.execute("generate-output")

        self.assertLessEqual(len(result.stdout.encode("utf-8")), 256 * 1024)
        self.assertLessEqual(len(result.stderr.encode("utf-8")), 256 * 1024)
        self.assertTrue(result.stdout.endswith("STDOUT-TAIL"))
        self.assertTrue(result.output["stdout"]["truncated"])
        self.assertTrue(result.output["stderr"]["truncated"])

    def test_native_scp_failure_output_is_bounded(self):
        completed = subprocess.CompletedProcess(
            ["scp"],
            1,
            "x" * 300_000 + "STDOUT-TAIL",
            "y" * 300_000 + "STDERR-TAIL",
        )
        client = NativeSSHClient("example.invalid", "user", key_file="example-key")

        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = Path(temp_dir) / "example.bin"
            local_path.write_bytes(b"data")
            with patch("native_ssh_client.subprocess.run", return_value=completed):
                result = client.upload(str(local_path), "/tmp/example.bin")

        self.assertLessEqual(len(result.stdout.encode("utf-8")), 256 * 1024)
        self.assertLessEqual(len(result.stderr.encode("utf-8")), 256 * 1024)
        self.assertTrue(result.stdout.endswith("STDOUT-TAIL"))
        self.assertTrue(result.output["stdout"]["truncated"])
        self.assertTrue(result.output["stderr"]["truncated"])


if __name__ == "__main__":
    unittest.main()
