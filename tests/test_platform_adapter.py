from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests import support
from platform_adapter import (
    AgentStatus,
    find_openssh,
    inspect_ssh_agent,
    normalize_local_path,
    normalize_platform,
    preserve_remote_path,
)


class PlatformAdapterTests(unittest.TestCase):
    def test_normalize_platform_maps_supported_system_names(self):
        self.assertEqual("windows", normalize_platform("Windows"))
        self.assertEqual("macos", normalize_platform("Darwin"))
        self.assertEqual("linux", normalize_platform("Linux"))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            normalize_platform("Plan9")

    def test_windows_prefers_system_openssh(self):
        which_calls = []
        expected = r"C:\Windows\System32\OpenSSH\ssh.exe"

        found = find_openssh(
            "windows",
            {"WINDIR": r"C:\Windows"},
            which=lambda name: which_calls.append(name),
            is_file=lambda path: path == expected,
        )

        self.assertEqual(expected, found)
        self.assertEqual([], which_calls)

    def test_macos_prefers_usr_bin_and_linux_uses_path(self):
        self.assertEqual(
            "/usr/bin/ssh",
            find_openssh(
                "macos",
                {},
                which=lambda _: "/opt/local/bin/ssh",
                is_file=lambda path: path == "/usr/bin/ssh",
            ),
        )
        self.assertEqual(
            "/opt/openssh/bin/ssh",
            find_openssh(
                "linux",
                {},
                which=lambda _: "/opt/openssh/bin/ssh",
                is_file=lambda _: False,
            ),
        )

    def test_windows_agent_check_uses_argument_arrays(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv[:2] == ["sc.exe", "query"]:
                return subprocess.CompletedProcess(argv, 0, "STATE : 4 RUNNING", "")
            return subprocess.CompletedProcess(argv, 0, "256 SHA256:test key", "")

        status = inspect_ssh_agent(
            "windows",
            {"WINDIR": r"C:\Windows"},
            runner=runner,
            is_file=lambda _: True,
            which=lambda name: name,
        )

        self.assertEqual(AgentStatus(True, True, True, "1 key loaded", []), status)
        self.assertEqual(["sc.exe", "query", "ssh-agent"], calls[0])
        self.assertIsInstance(calls[1], list)
        flattened = " ".join(part for call in calls for part in call)
        self.assertNotIn("powershell", flattened.lower())

    def test_posix_agent_without_socket_returns_platform_remediation(self):
        status = inspect_ssh_agent(
            "macos",
            {},
            runner=lambda _: self.fail("runner must not be called"),
        )

        self.assertFalse(status.available)
        self.assertFalse(status.running)
        self.assertIn("eval $(ssh-agent -s)", status.remediation)

    def test_local_and_remote_paths_are_handled_separately(self):
        self.assertEqual(
            r"C:\Users\me\file.txt",
            normalize_local_path(r"C:/Users/me/folder/../file.txt", "windows"),
        )
        self.assertEqual("/tmp/../var/data", preserve_remote_path("/tmp/../var/data"))

    def test_legacy_fallback_contains_no_powershell_command_wrapper(self):
        source = (support.LIB / "native_ssh_fallback.py").read_text(encoding="utf-8")
        self.assertNotIn("'powershell'", source.lower())
        self.assertNotIn('"powershell"', source.lower())


if __name__ == "__main__":
    unittest.main()
