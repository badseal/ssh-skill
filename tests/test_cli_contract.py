from __future__ import annotations

import io
import json
import unittest

from tests import support  # noqa: F401
from result_protocol import success_result
from ssh_skill import Dependencies, build_parser, main


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


if __name__ == "__main__":
    unittest.main()
