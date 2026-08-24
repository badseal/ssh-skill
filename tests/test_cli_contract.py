from __future__ import annotations

import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401
from result_protocol import error_result, success_result
from ssh_skill import (
    Dependencies,
    _forward_progress_line,
    _legacy_subprocess_handler,
    build_parser,
    main,
)


SUBCOMMANDS = [
    "exec", "upload", "download", "transfer", "cluster",
    "config", "tunnel", "daemon", "doctor",
]
COMMAND_CASES = (
    ("exec", ["exec", "example-host", "true"]),
    ("upload", ["upload", "example-host", "local", "/remote"]),
    ("download", ["download", "example-host", "/remote", "local"]),
    ("transfer", ["transfer", "source", "/a", "destination", "/b"]),
    ("cluster", ["cluster", "true"]),
    ("config", ["config", "list-servers"]),
    ("tunnel", ["tunnel", "list"]),
    ("daemon", ["daemon", "status", "example-host"]),
    ("doctor", ["doctor", "--json"]),
)


def handler_for(operation):
    return lambda args: success_result(operation, {"received": args.command_name})


FAKE_DEPENDENCIES = Dependencies(
    exec_handler=handler_for("exec"),
    upload_handler=handler_for("upload"),
    download_handler=handler_for("download"),
    transfer_handler=handler_for("transfer"),
    cluster_handler=handler_for("cluster"),
    config_handler=handler_for("config"),
    tunnel_handler=handler_for("tunnel"),
    daemon_handler=handler_for("daemon"),
    doctor_handler=handler_for("doctor"),
)


class CLIContractTests(unittest.TestCase):
    def test_all_subcommands_have_help(self):
        parser = build_parser()
        for command in SUBCOMMANDS:
            with self.subTest(command=command):
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args([command, "--help"])
                self.assertEqual(0, raised.exception.code)

    def test_main_emits_exactly_one_json_document(self):
        stdout = io.StringIO()

        code = main(
            ["exec", "example-host", "true", "--no-daemon"],
            dependencies=FAKE_DEPENDENCIES,
            stdout=stdout,
        )

        self.assertEqual(0, code)
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        result = json.loads(stdout.getvalue())
        self.assertEqual("1.0", result["schema_version"])
        self.assertEqual("exec", result["operation"])

    def test_all_subcommands_dispatch_to_their_operation(self):
        for operation, argv in COMMAND_CASES:
            with self.subTest(operation=operation):
                stdout = io.StringIO()

                code = main(argv, dependencies=FAKE_DEPENDENCIES, stdout=stdout)

                self.assertEqual(0, code)
                self.assertEqual(1, len(stdout.getvalue().splitlines()))
                result = json.loads(stdout.getvalue())
                self.assertEqual(operation, result["operation"])

    def test_handler_exception_is_one_redacted_json_document(self):
        dependencies = Dependencies(
            **{
                **FAKE_DEPENDENCIES.__dict__,
                "exec_handler": lambda args: (_ for _ in ()).throw(
                    RuntimeError("password=secret")
                ),
            }
        )
        stdout = io.StringIO()

        code = main(
            ["exec", "example-host", "true"],
            dependencies=dependencies,
            stdout=stdout,
        )

        self.assertEqual(1, code)
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        self.assertNotIn("secret", stdout.getvalue())
        self.assertEqual("cli_error", json.loads(stdout.getvalue())["error"]["code"])

    def test_invalid_arguments_are_reported_as_json(self):
        stdout = io.StringIO()

        code = main(["exec"], dependencies=FAKE_DEPENDENCIES, stdout=stdout)

        self.assertEqual(1, code)
        self.assertEqual("invalid_arguments", json.loads(stdout.getvalue())["error"]["code"])

    def test_unknown_exec_outcome_uses_stop_exit_code(self):
        dependencies = Dependencies(
            **{
                **FAKE_DEPENDENCIES.__dict__,
                "exec_handler": lambda args: error_result(
                    "exec",
                    code="outcome_unknown",
                    message="remote status is unavailable",
                    retryable=False,
                    outcome="unknown",
                ),
            }
        )
        stdout = io.StringIO()

        code = main(
            ["exec", "example-host", "apply-change", "--no-daemon"],
            dependencies=dependencies,
            stdout=stdout,
        )

        self.assertEqual(2, code)
        result = json.loads(stdout.getvalue())
        self.assertEqual("unknown", result["error"]["outcome"])
        self.assertFalse(result["error"]["retryable"])

    def test_legacy_subprocess_forwards_only_progress_before_completion(self):
        class QueueStream:
            def __init__(self):
                self.events = queue.Queue()

            def write(self, value):
                if value.strip():
                    self.events.put(value)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            child = root / "child.py"
            child.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "import sys",
                        "import time",
                        "release = Path(sys.argv[1])",
                        "print('password=secret', file=sys.stderr, flush=True)",
                        "print(json.dumps({'type': 'progress', 'operation': 'upload', 'file_path': '\\u6d4b\\u8bd5/\\u6587\\u4ef6.bin'}), file=sys.stderr, flush=True)",
                        "while not release.exists():",
                        "    time.sleep(0.01)",
                        "print(json.dumps({'success': True, 'exit_code': 0}), flush=True)",
                    ]
                ),
                encoding="utf-8",
            )
            progress_stream = QueueStream()
            holder = {}

            def invoke():
                holder["result"] = _legacy_subprocess_handler(
                    "upload", "child.py", [str(release)], legacy_flag=False
                )

            with patch("ssh_skill._SCRIPT_DIR", root), patch(
                "ssh_skill.sys.stderr", progress_stream
            ):
                worker = threading.Thread(target=invoke)
                worker.start()
                try:
                    forwarded = progress_stream.events.get(timeout=2)
                    self.assertTrue(worker.is_alive())
                finally:
                    release.touch()
                    worker.join(timeout=3)

            self.assertFalse(worker.is_alive())
            self.assertNotIn("secret", forwarded)
            event = json.loads(forwarded)
            self.assertEqual("progress", event["type"])
            self.assertEqual("测试/文件.bin", event["file_path"])
            self.assertTrue(holder["result"]["success"])

    def test_closed_progress_stream_does_not_break_pipe_drain(self):
        class ClosedStream:
            @staticmethod
            def write(value):
                raise OSError("stream is closed")

            @staticmethod
            def flush():
                raise OSError("stream is closed")

        line = b'{"type":"progress","operation":"upload","percent":50}\n'

        self.assertTrue(_forward_progress_line(line, ClosedStream()))


if __name__ == "__main__":
    unittest.main()
