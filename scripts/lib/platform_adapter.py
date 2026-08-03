from __future__ import annotations

import ntpath
import os
import platform
import posixpath
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class AgentStatus:
    available: bool
    running: bool
    key_count_known: bool
    message: str
    remediation: list[str]


def normalize_platform(system_name: str | None = None) -> str:
    name = (system_name or platform.system()).strip().lower()
    aliases = {"windows": "windows", "darwin": "macos", "macos": "macos", "linux": "linux"}
    try:
        return aliases[name]
    except KeyError as exc:
        raise ValueError(f"unsupported platform: {system_name or name}") from exc


def find_openssh(
    platform_name: str,
    env: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    environment = dict(os.environ if env is None else env)
    if platform_name == "windows":
        windir = environment.get("WINDIR") or environment.get("SystemRoot") or r"C:\Windows"
        candidate = ntpath.join(windir, "System32", "OpenSSH", "ssh.exe")
        return candidate if is_file(candidate) else which("ssh.exe")
    if platform_name == "macos" and is_file("/usr/bin/ssh"):
        return "/usr/bin/ssh"
    return which("ssh")


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        shell=False,
    )


def _find_ssh_add(
    platform_name: str,
    env: Mapping[str, str],
    which: Callable[[str], str | None],
    is_file: Callable[[str], bool],
) -> str | None:
    if platform_name == "windows":
        windir = env.get("WINDIR") or env.get("SystemRoot") or r"C:\Windows"
        candidate = ntpath.join(windir, "System32", "OpenSSH", "ssh-add.exe")
        return candidate if is_file(candidate) else which("ssh-add.exe")
    if platform_name == "macos" and is_file("/usr/bin/ssh-add"):
        return "/usr/bin/ssh-add"
    return which("ssh-add")


def inspect_ssh_agent(
    platform_name: str,
    env: Mapping[str, str] | None = None,
    *,
    runner: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> AgentStatus:
    environment = dict(os.environ if env is None else env)
    if platform_name == "windows":
        remediation = [
            "Start-Service ssh-agent",
            "Set-Service ssh-agent -StartupType Automatic",
            "ssh-add <private-key-path>",
        ]
        try:
            service = runner(["sc.exe", "query", "ssh-agent"])
        except (OSError, subprocess.SubprocessError) as exc:
            return AgentStatus(False, False, False, f"ssh-agent check failed: {exc}", remediation)
        if service.returncode != 0 or "RUNNING" not in service.stdout.upper():
            return AgentStatus(False, False, False, "ssh-agent service is not running", remediation)
    else:
        remediation = ["eval $(ssh-agent -s)", "ssh-add <private-key-path>"]
        if not environment.get("SSH_AUTH_SOCK"):
            return AgentStatus(False, False, False, "SSH_AUTH_SOCK is not set", remediation)

    ssh_add = _find_ssh_add(platform_name, environment, which, is_file)
    if not ssh_add:
        return AgentStatus(False, True, False, "ssh-add was not found", remediation)
    try:
        keys = runner([ssh_add, "-l"])
    except (OSError, subprocess.SubprocessError) as exc:
        return AgentStatus(False, True, False, f"ssh-add failed: {exc}", remediation)
    if keys.returncode == 0:
        count = len([line for line in keys.stdout.splitlines() if line.strip()])
        return AgentStatus(True, True, True, f"{count} key loaded" if count == 1 else f"{count} keys loaded", [])
    if keys.returncode == 1:
        return AgentStatus(False, True, True, "ssh-agent has no loaded keys", remediation)
    return AgentStatus(False, True, False, "ssh-agent status is unavailable", remediation)


def normalize_local_path(value: str, platform_name: str) -> str:
    return ntpath.normpath(value) if platform_name == "windows" else posixpath.normpath(value)


def preserve_remote_path(value: str) -> str:
    return value
