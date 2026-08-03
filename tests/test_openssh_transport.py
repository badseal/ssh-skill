from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import support  # noqa: F401
from openssh_transport import (
    OpenSSHOptions,
    build_ssh_argv,
    resolve_command_input,
    run_openssh,
)


class OpenSSHTransportTests(unittest.TestCase):
    def setUp(self):
        self.options = OpenSSHOptions(
            executable=r"C:\Windows\System32\OpenSSH\ssh.exe",
            config_path=r"C:\Users\user\.ssh\config",
        )

    def test_complex_command_is_one_argv_element(self):
        command = "printf '%s\\n' \"$HOME\"; echo `whoami`; echo 中文\nnext"

        argv = build_ssh_argv(self.options, "example-host", command)

        self.assertEqual(command, argv[-1])
        self.assertEqual("example-host", argv[-2])
        self.assertNotIn("powershell", " ".join(argv).lower())

    def test_runner_receives_argument_array_and_shell_false(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, b"ok\n", b"")

        result = run_openssh(
            self.options,
            "example-host",
            "echo $HOME",
            None,
            30,
            runner=runner,
        )

        self.assertTrue(result.success)
        self.assertEqual("ok\n", result.stdout)
        self.assertIsInstance(calls[0][0], list)
        self.assertIs(calls[0][1]["shell"], False)
        self.assertNotIn("text", calls[0][1])

    def test_command_file_is_sent_over_stdin_to_remote_sh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "command.sh"
            expected = "echo '$HOME'\nprintf '中文\\n'\n".encode("utf-8")
            path.write_bytes(expected)

            command, payload = resolve_command_input(
                command=None,
                command_file=path,
                use_stdin=False,
            )

        self.assertEqual("sh -s", command)
        self.assertEqual(expected, payload)

    def test_stdin_mode_reads_bytes_without_shell_parsing(self):
        command, payload = resolve_command_input(
            command=None,
            command_file=None,
            use_stdin=True,
            stdin=io.BytesIO(b"echo `id`\n"),
        )

        self.assertEqual("sh -s", command)
        self.assertEqual(b"echo `id`\n", payload)

    def test_command_sources_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_command_input("true", Path("command.sh"), False)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_command_input(None, None, False)

    def test_unsafe_host_key_mode_is_explicit(self):
        safe = build_ssh_argv(self.options, "example-host", "true")
        unsafe = build_ssh_argv(
            OpenSSHOptions(
                executable="ssh",
                unsafe_disable_host_key_checking=True,
            ),
            "example-host",
            "true",
        )

        self.assertIn("StrictHostKeyChecking=accept-new", safe)
        self.assertIn("StrictHostKeyChecking=no", unsafe)
        self.assertNotIn("UserKnownHostsFile=/dev/null", unsafe)

    def test_large_transport_output_is_bounded(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, b"x" * 300_000 + b"TAIL", b"")

        result = run_openssh(
            self.options, "example-host", "generate-output", None, 30, runner=runner
        )

        self.assertLessEqual(len(result.stdout.encode("utf-8")), 256 * 1024)
        self.assertTrue(result.output["stdout"]["truncated"])
        self.assertTrue(result.stdout.endswith("TAIL"))


if __name__ == "__main__":
    unittest.main()
