#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH命令执行CLI工具 v3.0

支持通过别名执行SSH命令，从标准 SSH config 和注释元数据中加载配置。
自动检测守护进程：有则走长连接，无则走直连。

用法：
    python ssh_execute.py <alias> <command> [--timeout TIMEOUT]
    python ssh_execute.py <alias> <command> --no-daemon

示例：
    python ssh_execute.py prod-web-01 "whoami && hostname"
    python ssh_execute.py DEV-002 "df -h" --timeout 60
"""

import sys
import os
import json
import socket
import argparse
import subprocess
import time
import uuid
from dataclasses import dataclass

# 添加lib到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from daemon_protocol import PROTOCOL_VERSION, FrameSendError, recv_frame, send_frame
from result_protocol import error_result


@dataclass(frozen=True)
class DaemonAttempt:
    disposition: str
    request_id: str
    result: dict | None = None


def may_fallback(attempt: DaemonAttempt) -> bool:
    return attempt.disposition == 'not_sent'


def daemon_attempt_to_result(attempt: DaemonAttempt) -> dict:
    if attempt.disposition == 'completed' and attempt.result is not None:
        return attempt.result
    if attempt.disposition == 'outcome_unknown':
        return error_result(
            'exec',
            code='outcome_unknown',
            message='daemon accepted the request but its final status is unavailable',
            retryable=False,
            outcome='unknown',
            request_id=attempt.request_id,
            transport='daemon',
        )
    return error_result(
        'exec',
        code='daemon_unavailable',
        message='daemon request was not sent',
        retryable=True,
        request_id=attempt.request_id,
        transport='daemon',
    )


def _send_message(sock, data):
    """发送带长度前缀的 JSON 消息"""
    return send_frame(sock, data)


def _recv_message(sock, timeout=None):
    """接收带长度前缀的 JSON 消息"""
    return recv_frame(sock, timeout=timeout)


def try_daemon_execute(
    alias,
    command,
    timeout,
    *,
    wait_timeout=None,
    request_id=None,
    daemon_info_reader=None,
    socket_factory=socket.socket,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """提交 daemon 请求；只有未发送任何字节时才允许降级。"""
    if daemon_info_reader is None:
        from ssh_daemon import read_daemon_info
        daemon_info_reader = read_daemon_info
    request_id = request_id or str(uuid.uuid4())
    info = daemon_info_reader(alias)
    if not info:
        return DaemonAttempt('not_sent', request_id)
    sock = None
    try:
        sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', info['port']))
    except Exception:
        if sock is not None:
            sock.close()
        return DaemonAttempt('not_sent', request_id)
    try:
        _send_message(sock, {
            'protocol_version': PROTOCOL_VERSION,
            'action': 'submit',
            'request_id': request_id,
            'command': command,
            'remote_timeout': timeout,
        })
    except FrameSendError as exc:
        sock.close()
        disposition = 'not_sent' if exc.bytes_sent == 0 else 'outcome_unknown'
        return DaemonAttempt(disposition, request_id)
    try:
        response = _recv_message(sock, timeout=5)
    except Exception:
        sock.close()
        return DaemonAttempt('outcome_unknown', request_id)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    if response.get('status') != 'ok':
        return DaemonAttempt('completed', request_id, {
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': response.get('error') or response.get('status', 'daemon protocol error'),
        })
    deadline = clock() + (wait_timeout if wait_timeout is not None else timeout + 5)
    request = response.get('request') or {}
    while request.get('state') in ('accepted', 'running') and clock() < deadline:
        sleep(0.1)
        status_sock = None
        try:
            status_sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            status_sock.settimeout(5)
            status_sock.connect(('127.0.0.1', info['port']))
            _send_message(status_sock, {
                'protocol_version': PROTOCOL_VERSION,
                'action': 'status',
                'request_id': request_id,
            })
            status_response = _recv_message(status_sock, timeout=5)
            request = status_response.get('request') or {}
        except Exception:
            return DaemonAttempt('outcome_unknown', request_id)
        finally:
            if status_sock is not None:
                try:
                    status_sock.close()
                except Exception:
                    pass
    if request.get('state') in ('succeeded', 'failed'):
        return DaemonAttempt('completed', request_id, request.get('result') or {})
    return DaemonAttempt('outcome_unknown', request_id)


def start_daemon_background(alias):
    """后台启动守护进程"""
    daemon_script = os.path.join(_script_dir, 'ssh_daemon.py')
    try:
        if os.name == 'nt':
            # Windows: 使用 CREATE_NO_WINDOW
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                [sys.executable, daemon_script, 'start', alias],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW
            )
        else:
            subprocess.Popen(
                [sys.executable, daemon_script, 'start', alias],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        # 等待守护进程启动
        import time
        for _ in range(10):
            time.sleep(0.3)
            from ssh_daemon import read_daemon_info
            if read_daemon_info(alias):
                return True
        return False
    except Exception:
        return False


def direct_execute(alias, command, timeout):
    """直连执行命令（智能选择客户端类型，支持降级到原生 SSH）"""
    from config_v3 import SSHConfigLoaderV3
    from native_ssh_fallback import should_use_native_ssh, execute_native_ssh, check_ssh_agent

    loader = SSHConfigLoaderV3()

    # 加载 SSH 配置
    ssh_config = loader.load_ssh_config(alias)
    metadata = {}
    try:
        metadata = loader.load_metadata(alias)
    except:
        pass

    # 检测是否应该降级到原生 SSH
    should_fallback, reason = should_use_native_ssh(ssh_config, metadata)

    if should_fallback:
        # 检查 ssh-agent 状态（如果涉及密钥认证）
        agent_available, agent_msg = check_ssh_agent()

        # 如果原因包含 passphrase 且 ssh-agent 不可用，给出提示但仍然尝试
        if 'passphrase' in reason.lower() and not agent_available:
            import sys
            print(f"\n⚠️  警告：检测到需要 passphrase 的密钥，但 ssh-agent 未配置", file=sys.stderr)
            print(f"ssh-agent 状态: {agent_msg}", file=sys.stderr)
            print(f"\n建议配置 ssh-agent 以避免每次输入密码：", file=sys.stderr)
            print(f"1. 启动 ssh-agent: eval $(ssh-agent)", file=sys.stderr)
            print(f"2. 添加密钥: ssh-add ~/.ssh/your_key", file=sys.stderr)
            print(f"\n现在将使用原生 SSH（需要交互式输入 passphrase）...\n", file=sys.stderr)

        # 使用原生 SSH 执行
        result = execute_native_ssh(alias, command, timeout)
        result['fallback_reason'] = reason
        return result

    # 使用智能选择：密钥认证 → NativeSSHClient，密码认证 → ParamikoClient
    client = loader.from_alias(alias)

    # 设置超时
    client.timeout = timeout

    result = client.execute(command)
    return {
        'success': result.success,
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr
    }


def main():
    parser = argparse.ArgumentParser(description='SSH command execution tool v3.0')
    parser.add_argument('alias', help='SSH host alias from ~/.ssh/config')
    parser.add_argument('command', help='Command to execute')
    parser.add_argument('--timeout', type=int, help='Timeout in seconds')
    parser.add_argument('--no-daemon', action='store_true',
                        help='Disable daemon mode, use direct SSH connection')

    args = parser.parse_args()
    timeout = args.timeout or 30

    try:
        result = None

        # 智能判断是否使用守护进程
        # 守护进程只对密码认证有意义（Paramiko），密钥认证使用原生 SSH 不需要守护进程
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        params = loader.get_connection_params(args.alias)

        has_key = params.get('key_file') is not None
        has_password = params.get('password') is not None
        use_daemon = has_password and not args.no_daemon  # 只有密码认证才使用守护进程

        if use_daemon:
            # 密码认证：尝试通过守护进程执行
            attempt = try_daemon_execute(args.alias, args.command, timeout)
            if attempt.disposition == 'completed':
                result = daemon_attempt_to_result(attempt)
            elif attempt.disposition == 'outcome_unknown':
                result = daemon_attempt_to_result(attempt)

            # 守护进程不可用，尝试后台启动
            if result is None and may_fallback(attempt):
                if start_daemon_background(args.alias):
                    attempt = try_daemon_execute(args.alias, args.command, timeout)
                    if not may_fallback(attempt):
                        result = daemon_attempt_to_result(attempt)

        # 仍然没有结果，使用直连（密钥认证会使用 NativeSSHClient）
        if result is None:
            result = direct_execute(args.alias, args.command, timeout)

        print(json.dumps(result, ensure_ascii=True, indent=2))
        sys.exit(0 if result.get('success') else 1)

    except FileNotFoundError as e:
        print(json.dumps({
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': f'Config not found: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': f'Invalid alias: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': f'Execution error: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
