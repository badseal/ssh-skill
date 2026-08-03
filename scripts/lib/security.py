from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import paramiko


SENSITIVE_KEYS = {
    "password",
    "passphrase",
    "key_passphrase",
    "private_key",
    "secret",
    "token",
}
_KNOWN_HOSTS_LOCK = threading.Lock()


@dataclass(frozen=True)
class HostKeyPolicy:
    strict_host_key_checking: str
    warnings: tuple[str, ...] = ()


def resolve_host_key_policy(*, unsafe_disable: bool = False) -> HostKeyPolicy:
    if unsafe_disable:
        return HostKeyPolicy("no", ("host_key_checking_disabled",))
    return HostKeyPolicy("accept-new")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if str(key).lower() in SENSITIVE_KEYS
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def _create_password_free_helper(platform_name: str, temp_dir: Path | None) -> Path:
    platform_name = platform_name.lower()
    suffix = ".cmd" if platform_name == "windows" else ".sh"
    directory = None if temp_dir is None else str(temp_dir)
    descriptor, helper_name = tempfile.mkstemp(
        prefix="ssh-skill-askpass-",
        suffix=suffix,
        dir=directory,
        text=True,
    )
    helper = Path(helper_name)
    if platform_name == "windows":
        content = (
            "@echo off\r\n"
            '"%SSH_SKILL_PYTHON%" -c "import os,sys;'
            "sys.stdout.write(os.environ.get('SSH_SKILL_ASKPASS_SECRET',''))"
            '"\r\n'
        )
    else:
        content = '#!/bin/sh\nprintf \'%s\\n\' "$SSH_SKILL_ASKPASS_SECRET"\n'

    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
        stream.write(content)
    if platform_name != "windows":
        helper.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return helper


@contextmanager
def askpass_environment(
    password: str,
    platform_name: str,
    temp_dir: Path | str | None = None,
) -> Iterator[dict[str, str]]:
    helper = _create_password_free_helper(
        platform_name,
        None if temp_dir is None else Path(temp_dir),
    )
    environment = os.environ.copy()
    environment.update(
        {
            "SSH_ASKPASS": str(helper),
            "SSH_ASKPASS_REQUIRE": "force",
            "SSH_SKILL_ASKPASS_SECRET": password,
            "SSH_SKILL_PYTHON": sys.executable,
        }
    )
    environment.setdefault("DISPLAY", ":0")
    try:
        yield environment
    finally:
        environment.pop("SSH_SKILL_ASKPASS_SECRET", None)
        helper.unlink(missing_ok=True)


class AcceptNewHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, known_hosts_path: Path):
        self.known_hosts_path = known_hosts_path

    def missing_host_key(self, client, hostname, key):
        existing = client._host_keys.lookup(hostname)
        if existing:
            expected = existing.get(key.get_name()) or next(iter(existing.values()))
            if expected != key:
                raise paramiko.BadHostKeyException(hostname, key, expected)
            return

        with _KNOWN_HOSTS_LOCK:
            if self.known_hosts_path.exists():
                refreshed = paramiko.HostKeys()
                refreshed.load(str(self.known_hosts_path))
                refreshed_entry = refreshed.lookup(hostname)
                if refreshed_entry:
                    expected = (
                        refreshed_entry.get(key.get_name())
                        or next(iter(refreshed_entry.values()))
                    )
                    if expected != key:
                        raise paramiko.BadHostKeyException(hostname, key, expected)
                    client._host_keys = refreshed
                    return
                client._host_keys = refreshed

            client._host_keys.add(hostname, key.get_name(), key)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.known_hosts_path.name}.",
                dir=str(self.known_hosts_path.parent),
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                client._host_keys.save(str(temporary_path))
                if os.name != "nt":
                    temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                os.replace(temporary_path, self.known_hosts_path)
            finally:
                temporary_path.unlink(missing_ok=True)


def configure_paramiko_host_keys(
    client: paramiko.SSHClient,
    known_hosts_path: Path | str | None = None,
    *,
    unsafe: bool = False,
) -> tuple[str, ...]:
    policy = resolve_host_key_policy(unsafe_disable=unsafe)
    if unsafe:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return policy.warnings

    client.load_system_host_keys()
    path = Path(known_hosts_path or Path.home() / ".ssh" / "known_hosts").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    client.load_host_keys(str(path))
    client.set_missing_host_key_policy(AcceptNewHostKeyPolicy(path))
    return policy.warnings
