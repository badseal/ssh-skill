from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import paramiko

from tests import support  # noqa: F401
from openssh_transport import OpenSSHOptions, build_ssh_argv, run_openssh
from security import (
    askpass_environment,
    configure_paramiko_host_keys,
    redact_sensitive,
    resolve_host_key_policy,
)
from ssh_config_manager_v3 import SSHConfigManager, cmd_create, cmd_list_servers
from ssh_server_transfer import direct_transfer, server_transfer
from fix_ssh_config import generate_updated_comments
from migrate_to_key_auth import migrate_to_key_auth
from config_v3 import SSHConfigLoaderV3


class SecurityTests(unittest.TestCase):
    def test_runtime_has_no_implicit_host_key_bypass(self):
        forbidden = (
            "StrictHostKeyChecking=no",
            "UserKnownHostsFile=/dev/null",
            "UserKnownHostsFile=NUL",
        )
        violations = []
        for path in support.SCRIPTS.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden:
                if pattern in text:
                    violations.append(f"{path.relative_to(support.ROOT)}: {pattern}")
            if path.name != "security.py" and (
                "set_missing_host_key_policy(paramiko.AutoAddPolicy())" in text
            ):
                violations.append(
                    f"{path.relative_to(support.ROOT)}: implicit AutoAddPolicy"
                )

        self.assertEqual([], violations)

    def test_default_openssh_policy_is_accept_new(self):
        args = build_ssh_argv(OpenSSHOptions("ssh"), "example-host", "true")

        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertNotIn("UserKnownHostsFile=NUL", args)
        self.assertNotIn("UserKnownHostsFile=/dev/null", args)

    def test_unsafe_policy_adds_machine_readable_warning(self):
        policy = resolve_host_key_policy(unsafe_disable=True)

        self.assertEqual("no", policy.strict_host_key_checking)
        self.assertIn("host_key_checking_disabled", policy.warnings)

        result = run_openssh(
            OpenSSHOptions("ssh", unsafe_disable_host_key_checking=True),
            "example-host",
            "true",
            None,
            30,
            runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, b"", b""
            ),
        )
        self.assertIn("host_key_checking_disabled", result.warnings)

    def test_paramiko_accept_new_persists_unknown_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            known_hosts = Path(temp_dir) / "known_hosts"
            client = paramiko.SSHClient()
            configure_paramiko_host_keys(client, known_hosts)

            first_key = paramiko.RSAKey.generate(1024)
            client._policy.missing_host_key(client, "example-host", first_key)
            self.assertIn("example-host", known_hosts.read_text(encoding="utf-8"))

            conflicting_key = paramiko.RSAKey.generate(1024)
            with self.assertRaises(paramiko.BadHostKeyException):
                client._policy.missing_host_key(
                    client, "example-host", conflicting_key
                )

    def test_explicit_unsafe_paramiko_policy_does_not_load_known_hosts(self):
        class Client:
            def __init__(self):
                self.loaded = False
                self.policy = None

            def load_system_host_keys(self):
                self.loaded = True

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

        client = Client()

        warnings = configure_paramiko_host_keys(client, unsafe=True)

        self.assertFalse(client.loaded)
        self.assertIsInstance(client.policy, paramiko.AutoAddPolicy)
        self.assertIn("host_key_checking_disabled", warnings)

    def test_askpass_file_contains_no_password_and_is_always_removed(self):
        secret = "p@ss & | < > % !"
        for platform_name in ("windows", "macos", "linux"):
            with self.subTest(platform=platform_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    helper_path = None
                    with self.assertRaisesRegex(RuntimeError, "stop"):
                        with askpass_environment(
                            secret, platform_name, Path(temp_dir)
                        ) as prepared:
                            helper_path = Path(prepared["SSH_ASKPASS"])
                            self.assertNotIn(
                                secret,
                                helper_path.read_text(encoding="utf-8"),
                            )
                            self.assertEqual(
                                secret, prepared["SSH_SKILL_ASKPASS_SECRET"]
                            )
                            raise RuntimeError("stop")
                    self.assertIsNotNone(helper_path)
                    self.assertFalse(helper_path.exists())

    def test_paramiko_client_creates_no_persistent_askpass_file(self):
        from paramiko_client import ParamikoClient

        client = ParamikoClient("example.invalid", "user", password="secret")

        self.assertIsNone(getattr(client, "_password_script", None))
        self.assertNotIn("StrictHostKeyChecking=no", client._build_scp_command(
            "source", "/remote", upload=True
        ))

    def test_recursive_redaction_covers_secret_fields(self):
        value = {
            "password": "secret",
            "nested": [{"passphrase": "phrase", "hostname": "example"}],
            "TOKEN": "token-value",
        }

        redacted = redact_sensitive(value)

        self.assertEqual("[REDACTED]", redacted["password"])
        self.assertEqual("[REDACTED]", redacted["nested"][0]["passphrase"])
        self.assertEqual("[REDACTED]", redacted["TOKEN"])
        self.assertEqual("example", redacted["nested"][0]["hostname"])

    def test_create_rejects_plaintext_password_before_writing(self):
        args = Namespace(
            alias="example-host",
            host="example.invalid",
            user="user",
            password="secret",
            key=None,
            port=22,
            jump=None,
            environment="development",
            description=None,
            tags=None,
            location=None,
        )
        stderr = io.StringIO()

        with (
            patch(
                "ssh_config_manager_v3.SSHConfigManager",
                side_effect=AssertionError("manager must not be constructed"),
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            cmd_create(args)

        result = json.loads(stderr.getvalue())
        self.assertEqual(
            "plaintext_password_write_disabled", result["error"]["code"]
        )
        self.assertNotIn("secret", stderr.getvalue())

    def test_export_redacts_legacy_password_and_warns(self):
        config_text = """# password: legacy-secret
Host legacy-host
    HostName example.invalid
    User user
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(config_text, encoding="utf-8")
            exported = SSHConfigManager(str(config_path)).export_config()

        serialized = json.dumps(exported, ensure_ascii=False)
        self.assertNotIn("legacy-secret", serialized)
        self.assertIn("deprecated_plaintext_password", exported["warnings"])
        self.assertEqual(
            "[REDACTED]", exported["hosts"][0]["metadata"]["password"]
        )

    def test_legacy_password_remains_read_only_with_migration_warning(self):
        config_text = """# password: legacy-secret
Host legacy-host
    HostName example.invalid
    User user
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(config_text, encoding="utf-8")
            params = SSHConfigLoaderV3(str(config_path)).get_connection_params(
                "legacy-host"
            )

        self.assertEqual("legacy-secret", params["password"])
        self.assertIn("deprecated_plaintext_password", params["warnings"])
        self.assertNotIn(
            "legacy-secret", json.dumps(redact_sensitive(params), ensure_ascii=False)
        )

    def test_list_reports_legacy_password_without_exposing_it(self):
        config_text = """# password: legacy-secret
Host legacy-host
    HostName example.invalid
    User user
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(config_text, encoding="utf-8")
            manager = SSHConfigManager(str(config_path))
            stdout = io.StringIO()
            with (
                patch("ssh_config_manager_v3.SSHConfigManager", return_value=manager),
                contextlib.redirect_stdout(stdout),
            ):
                cmd_list_servers(Namespace(environment=None, tags=None))

        serialized = stdout.getvalue()
        result = json.loads(serialized)
        self.assertNotIn("legacy-secret", serialized)
        self.assertIn("deprecated_plaintext_password", result["warnings"])

    def test_legacy_fix_does_not_reintroduce_plaintext_password(self):
        comments = generate_updated_comments(
            "example-host",
            {
                "description": "example",
                "environment": "test",
                "password": "legacy-secret",
            },
        )

        self.assertNotIn("legacy-secret", "".join(comments))

    def test_key_migration_removes_password_instead_of_hiding_it_in_tags(self):
        config_text = """
# ===== legacy-host =====
# tags: legacy
# password: legacy-secret
Host legacy-host
    HostName example.invalid
    User user
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(config_text, encoding="utf-8")
            with patch(
                "migrate_to_key_auth.os.path.expanduser",
                return_value=str(config_path),
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(migrate_to_key_auth("legacy-host", "id_example"))
            migrated = config_path.read_text(encoding="utf-8")

        self.assertNotIn("legacy-secret", migrated)
        self.assertNotIn("pwd:", migrated)
        self.assertIn("IdentityFile ~/.ssh/id_example", migrated)

    def test_server_transfer_does_not_enable_agent_forwarding_by_default(self):
        with (
            patch("ssh_server_transfer.validate_transfer", return_value=[]),
            patch(
                "ssh_server_transfer.direct_transfer",
                return_value={"success": True, "meta": {"warnings": []}},
            ) as direct,
        ):
            result = server_transfer(
                "source", "/source", "dest", "/dest",
                mode="direct", progress=False,
            )

        self.assertFalse(direct.call_args.kwargs["allow_agent_forwarding"])
        self.assertNotIn("agent_forwarding_enabled", result["meta"]["warnings"])

    def test_server_transfer_warns_when_agent_forwarding_is_explicit(self):
        with (
            patch("ssh_server_transfer.validate_transfer", return_value=[]),
            patch(
                "ssh_server_transfer.direct_transfer",
                return_value={"success": True, "meta": {"warnings": []}},
            ),
        ):
            result = server_transfer(
                "source", "/source", "dest", "/dest",
                mode="direct", progress=False, allow_agent_forwarding=True,
            )

        self.assertIn("agent_forwarding_enabled", result["meta"]["warnings"])

    def test_explicit_agent_forwarding_uses_the_executing_channel(self):
        class Stdout:
            def __init__(self, channel):
                self.channel = channel

            def __iter__(self):
                return iter(())

        class Stderr:
            @staticmethod
            def read():
                return b""

        class Channel:
            def __init__(self):
                self.command = None
                self.closed = False
                self.timeout = None

            def settimeout(self, timeout):
                self.timeout = timeout

            def get_pty(self):
                return None

            def exec_command(self, command):
                self.command = command

            def makefile(self, *args):
                return Stdout(self)

            def makefile_stderr(self, *args):
                return Stderr()

            @staticmethod
            def recv_exit_status():
                return 0

            def close(self):
                self.closed = True

        class Transport:
            def __init__(self, channel):
                self.channel = channel

            def open_session(self):
                return self.channel

        class Client:
            def __init__(self, channel):
                self.channel = channel
                self.exec_calls = 0

            def connect(self, **kwargs):
                return None

            def get_transport(self):
                return Transport(self.channel)

            def exec_command(self, *args, **kwargs):
                self.exec_calls += 1
                return None, Stdout(self.channel), Stderr()

            def close(self):
                return None

        channel = Channel()
        client = Client(channel)
        params = [
            {"hostname": "dest.invalid", "user": "dest", "port": 22},
            {
                "hostname": "source.invalid",
                "user": "source",
                "port": 22,
                "key_file": "example-key",
            },
        ]

        with (
            patch("ssh_server_transfer.get_connection_params", side_effect=params),
            patch("ssh_server_transfer.configure_paramiko_host_keys"),
            patch("paramiko.SSHClient", return_value=client),
            patch("paramiko.agent.AgentRequestHandler") as request_agent,
        ):
            result = direct_transfer(
                "source", "/source", "dest", "/dest",
                progress=False, allow_agent_forwarding=True,
            )

        self.assertTrue(result["success"])
        request_agent.assert_called_once_with(channel)
        self.assertIsNotNone(channel.command)
        self.assertEqual(300, channel.timeout)
        self.assertEqual(0, client.exec_calls)


if __name__ == "__main__":
    unittest.main()
