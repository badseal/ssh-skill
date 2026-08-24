#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR / "lib"))

from result_protocol import error_result, exit_code_for, success_result, write_result
from output_limits import BoundedText
from security import redact_sensitive


class CLIUsageError(ValueError):
    pass


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIUsageError(message)


Handler = Callable[[argparse.Namespace], dict[str, Any]]


@dataclass(frozen=True)
class Dependencies:
    exec_handler: Handler
    upload_handler: Handler
    download_handler: Handler
    transfer_handler: Handler
    cluster_handler: Handler
    config_handler: Handler
    tunnel_handler: Handler
    daemon_handler: Handler
    doctor_handler: Handler


def _subparsers(parser):
    return parser.add_subparsers(
        dest="command_name",
        required=True,
        parser_class=JSONArgumentParser,
    )


def _add_progress_flags(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--progress", dest="progress", action="store_true")
    group.add_argument("--no-progress", dest="progress", action="store_false")
    parser.set_defaults(progress=None)


def build_parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(prog="ssh_skill.py", description="SSH skill v4 CLI")
    commands = _subparsers(parser)

    execute = commands.add_parser("exec", help="Execute a remote command")
    execute.add_argument("alias")
    execute.add_argument("remote_command")
    execute.add_argument("--timeout", type=int, default=30)
    execute.add_argument("--no-daemon", action="store_true")

    upload = commands.add_parser("upload", help="Upload a file or directory")
    upload.add_argument("alias")
    upload.add_argument("local_path")
    upload.add_argument("remote_path")
    upload.add_argument("--resume", action="store_true")
    upload.add_argument("--recursive", action="store_true")
    _add_progress_flags(upload)

    download = commands.add_parser("download", help="Download a file or directory")
    download.add_argument("alias")
    download.add_argument("remote_path")
    download.add_argument("local_path")
    download.add_argument("--resume", action="store_true")
    download.add_argument("--recursive", action="store_true")
    _add_progress_flags(download)

    transfer = commands.add_parser("transfer", help="Transfer between SSH servers")
    transfer.add_argument("source_alias")
    transfer.add_argument("source_path")
    transfer.add_argument("dest_alias")
    transfer.add_argument("dest_path")
    transfer.add_argument("--mode", choices=("auto", "direct", "stream", "hybrid"), default="auto")
    transfer.add_argument("--use-rsync", action="store_true")
    transfer.add_argument("--allow-agent-forwarding", action="store_true")
    transfer.add_argument("--size-threshold", type=int, default=10)
    transfer.add_argument("--timeout", type=int, default=300)
    _add_progress_flags(transfer)

    cluster = commands.add_parser("cluster", help="Preview or apply a cluster command")
    cluster.add_argument("remote_command")
    cluster.add_argument("--hosts")
    cluster.add_argument("--environment")
    cluster.add_argument("--tags")
    cluster.add_argument("--parallel", action="store_true")
    cluster.add_argument("--timeout", type=int)
    cluster.add_argument("--health-check", action="store_true")
    cluster.add_argument("--max-workers", type=int, default=10)
    cluster.add_argument("--apply", action="store_true")
    cluster.add_argument("--confirm-production", action="store_true")

    for command in ("config", "tunnel", "daemon"):
        delegated = commands.add_parser(command, help=f"Run {command} operation")
        delegated.add_argument("arguments", nargs=argparse.REMAINDER)

    doctor = commands.add_parser("doctor", help="Run offline diagnostics")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--project-root")
    return parser


def _flag(arguments: list[str], enabled: bool, name: str) -> None:
    if enabled:
        arguments.append(name)


def _progress_flag(arguments: list[str], value: bool | None) -> None:
    if value is True:
        arguments.append("--progress")
    elif value is False:
        arguments.append("--no-progress")


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    value = stdout.strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _forward_progress_line(line: bytes, stream: TextIO) -> bool:
    try:
        event = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(event, dict) or event.get("type") != "progress":
        return False
    try:
        stream.write(
            json.dumps(
                redact_sensitive(event), ensure_ascii=True, separators=(",", ":")
            )
            + "\n"
        )
        stream.flush()
    except (OSError, UnicodeError, ValueError):
        pass
    return True


def _run_legacy_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout_collector = BoundedText()
    stderr_collector = BoundedText()

    def read_stderr() -> None:
        if process.stderr is None:
            return
        while True:
            line = process.stderr.readline(64 * 1024)
            if not line:
                break
            if not _forward_progress_line(line, sys.stderr):
                stderr_collector.feed(line)

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    if process.stdout is not None:
        for chunk in iter(lambda: process.stdout.read(64 * 1024), b""):
            stdout_collector.feed(chunk)
    return_code = process.wait()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        command,
        return_code,
        stdout_collector.finish().text,
        stderr_collector.finish().text,
    )


def _legacy_subprocess_handler(
    operation: str,
    script_name: str,
    arguments: list[str],
    *,
    legacy_flag: bool = True,
) -> dict[str, Any]:
    command = [sys.executable, str(_SCRIPT_DIR / script_name), *arguments]
    if legacy_flag:
        command.append("--legacy-json")
    completed = _run_legacy_process(command)
    parsed = _parse_json_output(completed.stdout) or _parse_json_output(completed.stderr)
    if parsed and parsed.get("schema_version") == "1.0":
        return parsed
    succeeded = completed.returncode == 0 and not (
        isinstance(parsed, dict) and parsed.get("success") is False
    )
    if succeeded:
        return success_result(operation, parsed or {"exit_code": completed.returncode})
    return error_result(
        operation,
        code=f"{operation}_failed",
        message=f"{operation} command failed",
        data={"exit_code": completed.returncode},
    )


def _exec_handler(args):
    from ssh_execute import run_exec

    return run_exec(
        args.alias,
        args.remote_command,
        timeout=args.timeout,
        no_daemon=args.no_daemon,
    )


def _upload_handler(args):
    values = [args.alias, args.local_path, args.remote_path]
    _flag(values, args.resume, "--resume")
    _flag(values, args.recursive, "--recursive")
    _progress_flag(values, args.progress)
    return _legacy_subprocess_handler("upload", "ssh_upload.py", values)


def _download_handler(args):
    values = [args.alias, args.remote_path, args.local_path]
    _flag(values, args.resume, "--resume")
    _flag(values, args.recursive, "--recursive")
    _progress_flag(values, args.progress)
    return _legacy_subprocess_handler("download", "ssh_download.py", values)


def _transfer_handler(args):
    values = [args.source_alias, args.source_path, args.dest_alias, args.dest_path]
    values.extend(["--mode", args.mode, "--size-threshold", str(args.size_threshold), "--timeout", str(args.timeout)])
    _flag(values, args.use_rsync, "--use-rsync")
    _flag(values, args.allow_agent_forwarding, "--allow-agent-forwarding")
    _progress_flag(values, args.progress)
    return _legacy_subprocess_handler("transfer", "ssh_server_transfer.py", values)


def _cluster_handler(args):
    values = [args.remote_command]
    for flag, value in (
        ("--hosts", args.hosts),
        ("--environment", args.environment),
        ("--tags", args.tags),
        ("--timeout", args.timeout),
        ("--max-workers", args.max_workers),
    ):
        if value is not None:
            values.extend([flag, str(value)])
    for flag, enabled in (
        ("--parallel", args.parallel),
        ("--health-check", args.health_check),
        ("--apply", args.apply),
        ("--confirm-production", args.confirm_production),
    ):
        _flag(values, enabled, flag)
    return _legacy_subprocess_handler(
        "cluster", "ssh_cluster.py", values, legacy_flag=False
    )


def _delegated_handler(operation: str, script_name: str) -> Handler:
    return lambda args: _legacy_subprocess_handler(
        operation, script_name, list(args.arguments)
    )


def _doctor_handler(args):
    from doctor import create_default_context, run_doctor

    project_root = Path(args.project_root) if args.project_root else Path.cwd()
    context = create_default_context(
        current_root=_SCRIPT_DIR.parent,
        project_root=project_root,
    )
    return run_doctor(context)


def default_dependencies() -> Dependencies:
    return Dependencies(
        exec_handler=_exec_handler,
        upload_handler=_upload_handler,
        download_handler=_download_handler,
        transfer_handler=_transfer_handler,
        cluster_handler=_cluster_handler,
        config_handler=_delegated_handler("config", "ssh_config_manager_v3.py"),
        tunnel_handler=_delegated_handler("tunnel", "ssh_tunnel.py"),
        daemon_handler=_delegated_handler("daemon", "ssh_daemon.py"),
        doctor_handler=_doctor_handler,
    )


def run(
    argv: Sequence[str],
    dependencies: Dependencies | None = None,
) -> dict[str, Any]:
    args = build_parser().parse_args(list(argv))
    dependencies = dependencies or default_dependencies()
    handler = getattr(dependencies, f"{args.command_name}_handler")
    result = handler(args)
    if not isinstance(result, dict):
        raise TypeError("operation handler must return a dictionary")
    if result.get("schema_version") == "1.0":
        return result
    return success_result(args.command_name, result)


def _exception_to_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, CLIUsageError):
        return error_result(
            "cli", code="invalid_arguments", message=str(exc), retryable=False
        )
    return error_result(
        "cli",
        code="cli_error",
        message=f"{type(exc).__name__}: operation failed",
        retryable=False,
    )


def main(
    argv: Sequence[str] | None = None,
    dependencies: Dependencies | None = None,
    stdout: TextIO = sys.stdout,
) -> int:
    try:
        result = run(sys.argv[1:] if argv is None else argv, dependencies)
    except Exception as exc:
        result = _exception_to_result(exc)
    result = redact_sensitive(result)
    write_result(result, stream=stdout)
    return exit_code_for(result)


def delegate_legacy_entrypoint(
    command_name: str,
    argv: Sequence[str],
    *,
    unified_main: Callable[[Sequence[str]], int] = main,
    legacy_main: Callable[[Sequence[str]], int],
) -> int:
    arguments = list(argv)
    if "--legacy-json" not in arguments:
        return unified_main([command_name, *arguments])
    arguments.remove("--legacy-json")
    try:
        result = legacy_main(arguments)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
