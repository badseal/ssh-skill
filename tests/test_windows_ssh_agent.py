from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401
from windows_ssh_agent import (
    check_windows_ssh_agent,
    enable_windows_ssh_agent_auto_start,
    start_windows_ssh_agent,
)


class WindowsSSHAgentTests(unittest.TestCase):
    def test_status_uses_sc_argument_arrays(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1] == "query":
                return subprocess.CompletedProcess(
                    argv, 0, "STATE : 4 RUNNING\n", ""
                )
            return subprocess.CompletedProcess(
                argv, 0, "START_TYPE : 2 AUTO_START\n", ""
            )

        with patch("windows_ssh_agent.os.name", "nt"):
            result = check_windows_ssh_agent(runner=runner)

        self.assertTrue(result["available"])
        self.assertTrue(result["running"])
        self.assertTrue(result["auto_start"])
        self.assertEqual(["sc.exe", "query", "ssh-agent"], calls[0][0])
        self.assertEqual(["sc.exe", "qc", "ssh-agent"], calls[1][0])
        self.assertFalse(calls[0][1]["shell"])
        self.assertNotIn("powershell", repr(calls).lower())

    def test_start_and_auto_start_use_sc_argument_arrays(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch("windows_ssh_agent.os.name", "nt"):
            started = start_windows_ssh_agent(runner=runner)
            configured = enable_windows_ssh_agent_auto_start(runner=runner)

        self.assertTrue(started["success"])
        self.assertTrue(configured["success"])
        self.assertEqual(["sc.exe", "start", "ssh-agent"], calls[0][0])
        self.assertEqual(
            ["sc.exe", "config", "ssh-agent", "start=", "auto"], calls[1][0]
        )
        self.assertTrue(all(call[1]["shell"] is False for call in calls))

    def test_source_has_no_powershell_command_wrapper(self):
        source = (support.LIB / "windows_ssh_agent.py").read_text(encoding="utf-8")
        self.assertNotIn("'-Command'", source)
        self.assertNotIn('"-Command"', source)


if __name__ == "__main__":
    unittest.main()
