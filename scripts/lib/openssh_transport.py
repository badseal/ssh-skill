from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

from output_limits import OutputLimits, collect_text
from security import resolve_host_key_policy


@dataclass(frozen=True)
class OpenSSHOptions:
    executable: str
    config_path: str | None = None
    port: int | None = None
    key_file: str | None = None
    proxy_jump: str | None = None
    forward_agent: bool = False
    batch_mode: bool = True
    strict_host_key_checking: str = "accept-new"
    known_hosts_file: str | None = None
    unsafe_disable_host_key_checking: bool = False


@dataclass(frozen=True)
class TransportResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    output: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    retryable: bool | None = None
    outcome: str | None = None

    @classmethod
    def from_completed_process(
        cls,
        completed: subprocess.CompletedProcess[Any],
        limits: OutputLimits | None = None,
        warnings: tuple[str, ...] = (),
    ) -> "TransportResult":
        stdout = collect_text(_decode(completed.stdout), limits)
        stderr = collect_text(_decode(completed.stderr), limits)
        success = completed.returncode == 0
        return cls(
            success=success,
            stdout=stdout.text,
            stderr=stderr.text,
            exit_code=completed.returncode,
            output={"stdout": stdout.to_meta(), "stderr": stderr.to_meta()},
            warnings=warnings,
            error_code=None if success else "remote_command_failed",
            retryable=None if success else False,
            outcome=None if success else "failed",
        )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def build_ssh_argv(options: OpenSSHOptions, target: str, command: str | None = None) -> list[str]:
    policy = resolve_host_key_policy(
        unsafe_disable=options.unsafe_disable_host_key_checking
    )
    strict = (
        policy.strict_host_key_checking
        if options.unsafe_disable_host_key_checking
        else options.strict_host_key_checking
    )
    argv = [options.executable]
    if options.config_path:
        argv.extend(["-F", options.config_path])
    if options.port is not None:
        argv.extend(["-p", str(options.port)])
    if options.key_file:
        argv.extend(["-i", options.key_file])
    argv.extend(["-o", f"BatchMode={'yes' if options.batch_mode else 'no'}"])
    argv.extend(["-o", f"StrictHostKeyChecking={strict}"])
    if options.known_hosts_file:
        argv.extend(["-o", f"UserKnownHostsFile={options.known_hosts_file}"])
    if options.proxy_jump:
        argv.extend(["-o", f"ProxyJump={options.proxy_jump}"])
    if options.forward_agent:
        argv.extend(["-o", "ForwardAgent=yes"])
    argv.append(target)
    if command is not None:
        argv.append(command)
    return argv


def run_openssh(
    options: OpenSSHOptions,
    target: str,
    command: str | None,
    stdin_bytes: bytes | None,
    timeout: int | float | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    limits: OutputLimits | None = None,
) -> TransportResult:
    argv = build_ssh_argv(options, target, command)
    policy = resolve_host_key_policy(
        unsafe_disable=options.unsafe_disable_host_key_checking
    )
    try:
        completed = runner(
            argv,
            input=stdin_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return TransportResult(
            False,
            "",
            f"command timed out after {timeout} seconds",
            -1,
            warnings=policy.warnings,
            error_code="outcome_unknown",
            retryable=False,
            outcome="unknown",
        )
    except OSError as exc:
        return TransportResult(
            False,
            "",
            f"OpenSSH execution failed: {exc}",
            -1,
            warnings=policy.warnings,
            error_code="openssh_execution_failed",
            retryable=False,
            outcome="failed",
        )
    return TransportResult.from_completed_process(
        completed, limits=limits, warnings=policy.warnings
    )


def resolve_command_input(
    command: str | None,
    command_file: Path | str | None,
    use_stdin: bool,
    *,
    stdin: BinaryIO | None = None,
) -> tuple[str, bytes | None]:
    selected = int(command is not None) + int(command_file is not None) + int(use_stdin)
    if selected != 1:
        raise ValueError("exactly one of command, command_file, or stdin is required")
    if command is not None:
        return command, None
    if command_file is not None:
        return "sh -s", Path(command_file).read_bytes()
    source = stdin if stdin is not None else sys.stdin.buffer
    return "sh -s", source.read()
