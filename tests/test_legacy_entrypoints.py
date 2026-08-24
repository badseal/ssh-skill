from __future__ import annotations

import contextlib
import io
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401
from result_protocol import success_result
from ssh_execute import _normalize_exec_result, direct_execute, main as execute_main
from ssh_skill import delegate_legacy_entrypoint


LEGACY_ENTRYPOINTS = (
    ("ssh_upload", "upload", ["example", "local", "/remote"]),
    ("ssh_download", "download", ["example", "/remote", "local"]),
    (
        "ssh_server_transfer",
        "transfer",
        ["source", "/source", "destination", "/destination"],
    ),
    ("ssh_config_manager_v3", "config", ["list-servers"]),
    ("ssh_tunnel", "tunnel", ["list"]),
    ("ssh_daemon", "daemon", ["status", "example"]),
)
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


class LegacyEntrypointTests(unittest.TestCase):
    def test_ssh_execute_keeps_positionals_and_defaults_to_v4(self):
        stdout = io.StringIO()
        executor = lambda alias, command, timeout, no_daemon: success_result(
            "exec",
            {"alias": alias, "command": command, "exit_code": 0,
             "stdout": "ok", "stderr": ""},
        )

        code = execute_main(
            ["example-host", "true", "--no-daemon"],
            stdout=stdout,
            executor=executor,
        )

        result = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("1.0", result["schema_version"])
        self.assertEqual("example-host", result["data"]["alias"])

    def test_ssh_execute_legacy_json_is_explicit(self):
        stdout = io.StringIO()
        executor = lambda alias, command, timeout, no_daemon: success_result(
            "exec",
            {"exit_code": 0, "stdout": "ok", "stderr": ""},
        )

        code = execute_main(
            ["example-host", "true", "--legacy-json"],
            stdout=stdout,
            executor=executor,
        )

        result = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertNotIn("schema_version", result)
        self.assertEqual("ok", result["stdout"])

    def test_ssh_execute_argument_errors_use_v4_json(self):
        stdout = io.StringIO()

        code = execute_main([], stdout=stdout)

        self.assertEqual(1, code)
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        result = json.loads(stdout.getvalue())
        self.assertEqual("1.0", result["schema_version"])
        self.assertEqual("invalid_arguments", result["error"]["code"])

    def test_direct_timeout_metadata_survives_normalization(self):
        class Client:
            timeout = 30

            @staticmethod
            def execute(command):
                return SimpleNamespace(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr="command timed out",
                    output={},
                    error_code="outcome_unknown",
                    retryable=False,
                    outcome="unknown",
                )

        class Loader:
            @staticmethod
            def load_ssh_config(alias):
                return {}

            @staticmethod
            def load_metadata(alias):
                return {}

            @staticmethod
            def from_alias(alias):
                return Client()

        with patch("config_v3.SSHConfigLoaderV3", return_value=Loader()), patch(
            "native_ssh_fallback.should_use_native_ssh", return_value=(False, "")
        ):
            raw = direct_execute("example-host", "apply-change", 30)

        result = _normalize_exec_result(raw, "example-host", "apply-change")

        self.assertEqual("outcome_unknown", result["error"]["code"])
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual("unknown", result["error"]["outcome"])

    def test_legacy_delegate_uses_unified_cli_unless_flag_is_explicit(self):
        calls = []

        code = delegate_legacy_entrypoint(
            "upload",
            ["example", "local", "/remote"],
            unified_main=lambda argv: calls.append(("unified", argv)) or 0,
            legacy_main=lambda argv: calls.append(("legacy", argv)) or 7,
        )
        legacy_code = delegate_legacy_entrypoint(
            "upload",
            ["example", "local", "/remote", "--legacy-json"],
            unified_main=lambda argv: calls.append(("unified", argv)) or 0,
            legacy_main=lambda argv: calls.append(("legacy", argv)) or 7,
        )

        self.assertEqual(0, code)
        self.assertEqual(7, legacy_code)
        self.assertEqual(
            [
                ("unified", ["upload", "example", "local", "/remote"]),
                ("legacy", ["example", "local", "/remote"]),
            ],
            calls,
        )

    def test_all_legacy_entrypoints_delegate_with_explicit_argv(self):
        for module_name, command_name, argv in LEGACY_ENTRYPOINTS:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                with patch(
                    "ssh_skill.delegate_legacy_entrypoint", return_value=23
                ) as delegate:
                    code = module.main(argv)

                self.assertEqual(23, code)
                call = delegate.call_args
                self.assertEqual(command_name, call.args[0])
                self.assertEqual(argv, call.args[1])
                self.assertTrue(callable(call.kwargs["legacy_main"]))

    def test_legacy_parsers_consume_passed_argv(self):
        for module_name, _command_name, _argv in LEGACY_ENTRYPOINTS:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                captured = {}

                def capture_delegate(command_name, argv, *, legacy_main):
                    captured["legacy_main"] = legacy_main
                    return 0

                with patch(
                    "ssh_skill.delegate_legacy_entrypoint",
                    side_effect=capture_delegate,
                ):
                    module.main(["--help"])

                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        captured["legacy_main"](["--help"])
                self.assertEqual(0, raised.exception.code)

    def test_legacy_script_argument_errors_are_single_v4_json(self):
        script_names = ["ssh_execute.py"] + [
            f"{module_name}.py" for module_name, _command_name, _argv in LEGACY_ENTRYPOINTS
        ]
        for script_name in script_names:
            with self.subTest(script=script_name):
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / script_name)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )

                self.assertEqual(1, completed.returncode)
                self.assertEqual("", completed.stderr)
                self.assertEqual(1, len(completed.stdout.splitlines()))
                result = json.loads(completed.stdout)
                self.assertEqual("1.0", result["schema_version"])
                self.assertFalse(result["success"])

    def test_legacy_delegate_normalizes_non_integer_system_exit(self):
        def legacy_main(argv):
            raise SystemExit("legacy failure")

        code = delegate_legacy_entrypoint(
            "upload",
            ["example", "local", "/remote", "--legacy-json"],
            unified_main=lambda argv: 0,
            legacy_main=legacy_main,
        )

        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
