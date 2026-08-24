#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 长连接守护进程 v3.0

在本地维护到远程服务器的 Paramiko 长连接，通过 TCP 接受命令请求。
自动启动、空闲超时自动退出，对调用方完全透明。

v3.0 变更：使用别名（alias）标识服务器，从 ~/.ssh/config 读取配置

用法：
    python ssh_daemon.py start <alias>
    python ssh_daemon.py status <alias>
    python ssh_daemon.py stop <alias>

示例：
    python ssh_daemon.py start DEV-002
    python ssh_daemon.py status DEV-002
    python ssh_daemon.py stop DEV-002
"""

import sys
import os
import json
import socket
import threading
import time
import hashlib
import signal
import tempfile
import traceback
import argparse
from pathlib import Path

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from daemon_protocol import PROTOCOL_VERSION, fingerprint_execution, recv_frame, send_frame
from output_limits import bound_result_output
from request_registry import RequestIdConflict, RequestRegistry
from security import configure_paramiko_host_keys


# === 常量 ===
DAEMON_DIR = os.path.join(tempfile.gettempdir(), 'ssh_daemon')
IDLE_TIMEOUT = 1800  # 30 分钟空闲自动退出
HEARTBEAT_INTERVAL = 60  # 60 秒心跳检测
RECONNECT_MAX_RETRIES = 3
RECV_BUFFER = 65536


def get_daemon_id(alias: str) -> str:
    """根据别名生成守护进程唯一标识"""
    # 使用别名的 MD5 前 12 位作为文件名，避免特殊字符问题
    return hashlib.md5(alias.lower().encode('utf-8')).hexdigest()[:12]


def get_daemon_info_path(alias: str) -> str:
    """获取守护进程信息文件路径"""
    os.makedirs(DAEMON_DIR, exist_ok=True)
    return os.path.join(DAEMON_DIR, f'{get_daemon_id(alias)}.json')


def read_daemon_info(alias: str) -> dict:
    """读取守护进程信息，返回 None 表示不存在或无效"""
    info_path = get_daemon_info_path(alias)
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        # 检查进程是否存活
        pid = info.get('pid')
        if pid and _is_process_alive(pid):
            return info
        # 进程已死，清理信息文件
        os.remove(info_path)
        return None
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def _send_message(sock: socket.socket, data: dict):
    """发送带长度前缀的 JSON 消息"""
    return send_frame(sock, data)


def _recv_message(sock: socket.socket, timeout: float = None) -> dict:
    """接收带长度前缀的 JSON 消息"""
    return recv_frame(sock, timeout=timeout)


class SSHDaemon:
    """SSH 长连接守护进程 v3.0"""

    def __init__(self, alias: str, idle_timeout: int = IDLE_TIMEOUT):
        self.alias = alias
        self.idle_timeout = idle_timeout
        self._last_activity = time.time()
        self._running = False
        self._server_socket = None
        self._ssh_client = None
        self._lock = threading.Lock()
        self._requests = RequestRegistry()
        self._connection_params = None  # 缓存连接参数

    def start(self):
        """启动守护进程"""
        # 加载配置
        self._load_config()

        # 建立 SSH 连接
        self._connect_ssh()

        # 启动 TCP 服务
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(('127.0.0.1', 0))  # 随机端口
        self._server_socket.listen(5)
        self._server_socket.settimeout(5.0)  # accept 超时，用于检查空闲

        port = self._server_socket.getsockname()[1]
        self._running = True

        # 写入守护进程信息
        host_info = self._get_host_info()
        info = {
            'pid': os.getpid(),
            'port': port,
            'alias': self.alias,
            'host': host_info,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'idle_timeout': self.idle_timeout
        }
        info_path = get_daemon_info_path(self.alias)
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

        # 输出启动信息到 stdout
        try:
            print(json.dumps({
                'status': 'started',
                'pid': os.getpid(),
                'port': port,
                'alias': self.alias,
                'host': host_info
            }, ensure_ascii=False))
            sys.stdout.flush()
        except Exception:
            pass  # stdout 不可用（后台进程），忽略

        # 启动心跳线程
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        # 启动空闲检测线程
        idle_thread = threading.Thread(target=self._idle_check_loop, daemon=True)
        idle_thread.start()

        # 主循环：接受连接
        try:
            while self._running:
                try:
                    client_sock, addr = self._server_socket.accept()
                    t = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock,),
                        daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    if self._running:
                        raise
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _load_config(self):
        """从 SSH config 加载连接参数"""
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        self._connection_params = loader.get_connection_params(self.alias)

    def _get_host_info(self) -> str:
        """获取目标服务器信息"""
        if self._connection_params:
            user = self._connection_params.get('user', 'unknown')
            host = self._connection_params.get('hostname', 'unknown')
            return f"{user}@{host}"
        return "unknown"

    def _connect_ssh(self):
        """建立 SSH 连接"""
        import paramiko

        params = self._connection_params
        if not params:
            raise ValueError("连接参数未加载，请先调用 _load_config()")

        host = params['hostname']
        user = params['user']
        port = params['port']
        password = params.get('password')
        key_file = params.get('key_file')

        # 解析密钥路径
        if key_file:
            key_file = os.path.expanduser(key_file)
            key_file = os.path.abspath(key_file)

        client = paramiko.SSHClient()
        configure_paramiko_host_keys(client)

        connect_kwargs = {
            'hostname': host,
            'port': port,
            'username': user,
            'timeout': params.get('timeout', 30),
        }

        if password:
            connect_kwargs['password'] = password
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
        elif key_file:
            # 尝试多种密钥类型
            pkey = None
            for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                try:
                    pkey = key_class.from_private_key_file(key_file)
                    break
                except Exception:
                    continue
            if pkey is None:
                raise ValueError(f"无法加载密钥文件: {key_file}")
            connect_kwargs['pkey'] = pkey
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
        else:
            raise ValueError("必须提供 password 或 key_file")

        client.connect(**connect_kwargs)
        self._ssh_client = client

    def _reconnect_ssh(self) -> bool:
        """重连 SSH"""
        for attempt in range(RECONNECT_MAX_RETRIES):
            try:
                if self._ssh_client:
                    try:
                        self._ssh_client.close()
                    except Exception:
                        pass
                self._connect_ssh()
                return True
            except Exception as e:
                wait = (attempt + 1) * 2
                print(f"[DAEMON] 重连失败 (尝试 {attempt+1}/{RECONNECT_MAX_RETRIES}): {e}",
                      file=sys.stderr)
                time.sleep(wait)
        return False

    def _is_ssh_alive(self) -> bool:
        """检查 SSH 连接是否存活"""
        try:
            if not self._ssh_client:
                return False
            transport = self._ssh_client.get_transport()
            if not transport or not transport.is_active():
                return False
            transport.send_ignore()
            return True
        except Exception:
            return False

    def _handle_client(self, client_sock: socket.socket):
        """处理单个客户端连接"""
        try:
            client_sock.settimeout(10)
            request = _recv_message(client_sock, timeout=10)
            action = request.get('action', '')

            if action == 'ping':
                _send_message(client_sock, {
                    'status': 'ok',
                    'pid': os.getpid(),
                    'alias': self.alias,
                    'ssh_alive': self._is_ssh_alive(),
                    'uptime': int(time.time() - self._start_time),
                    'idle_seconds': int(time.time() - self._last_activity)
                })

            elif action == 'submit':
                self._last_activity = time.time()
                _send_message(client_sock, self._submit_execution(request))

            elif action == 'status':
                record = self._requests.get(request.get('request_id', ''))
                _send_message(client_sock, {
                    'protocol_version': PROTOCOL_VERSION,
                    'status': 'not_found' if record is None else 'ok',
                    'request': None if record is None else record.to_dict(),
                })

            elif action == 'execute':
                _send_message(client_sock, {
                    'protocol_version': PROTOCOL_VERSION,
                    'status': 'protocol_upgrade_required',
                    'error': 'v4 requests require action=submit and request_id',
                })

            elif action == 'shutdown':
                _send_message(client_sock, {'status': 'shutting_down'})
                self._running = False

            else:
                _send_message(client_sock, {
                    'success': False,
                    'exit_code': -1,
                    'stdout': '',
                    'stderr': f'未知操作: {action}'
                })

        except Exception as e:
            try:
                _send_message(client_sock, {
                    'success': False,
                    'exit_code': -1,
                    'stdout': '',
                    'stderr': f'守护进程处理错误: {str(e)}'
                })
            except Exception:
                pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _submit_execution(self, request: dict, thread_factory=threading.Thread) -> dict:
        if request.get('protocol_version') != PROTOCOL_VERSION:
            return {'protocol_version': PROTOCOL_VERSION, 'status': 'unsupported_protocol'}
        request_id = request.get('request_id', '')
        command = request.get('command')
        remote_timeout = request.get('remote_timeout', 30)
        if not request_id or not isinstance(command, str) or not command:
            return {'protocol_version': PROTOCOL_VERSION, 'status': 'invalid_request'}
        if not isinstance(remote_timeout, int) or remote_timeout <= 0:
            return {'protocol_version': PROTOCOL_VERSION, 'status': 'invalid_timeout'}
        fingerprint = fingerprint_execution(command, remote_timeout)
        try:
            record, created = self._requests.accept(request_id, fingerprint)
        except RequestIdConflict:
            return {'protocol_version': PROTOCOL_VERSION, 'status': 'request_id_conflict'}
        if created:
            worker = thread_factory(
                target=self._run_request,
                args=(request_id, command, remote_timeout),
                daemon=True,
            )
            worker.start()
        current = self._requests.get(request_id) or record
        return {
            'protocol_version': PROTOCOL_VERSION,
            'status': 'ok',
            'request': current.to_dict(),
        }

    def _run_request(self, request_id: str, command: str, remote_timeout: int) -> None:
        queued_at = time.monotonic()
        with self._lock:
            queue_ms = int((time.monotonic() - queued_at) * 1000)
            self._requests.mark_running(request_id, queue_ms=queue_ms)
            started_at = time.monotonic()
            result = bound_result_output(
                self._execute_command_unlocked(command, remote_timeout)
            )
            execution_ms = int((time.monotonic() - started_at) * 1000)
        state = 'succeeded' if result.get('success') else 'failed'
        self._requests.finish(
            request_id,
            state=state,
            result=result,
            execution_ms=execution_ms,
        )

    def _execute_command(self, command: str, timeout: int) -> dict:
        """通过长连接执行远程命令"""
        with self._lock:
            return self._execute_command_unlocked(command, timeout)

    def _execute_command_unlocked(self, command: str, timeout: int) -> dict:
        if not self._is_ssh_alive():
            if not self._reconnect_ssh():
                return {
                    'success': False,
                    'exit_code': -1,
                    'stdout': '',
                    'stderr': 'SSH 连接已断开且重连失败'
                }
        try:
            stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=timeout)
            stdout_text = stdout.read().decode('utf-8', errors='replace')
            stderr_text = stderr.read().decode('utf-8', errors='replace')
            exit_code = stdout.channel.recv_exit_status()
            return {
                'success': exit_code == 0,
                'exit_code': exit_code,
                'stdout': stdout_text,
                'stderr': stderr_text
            }
        except Exception as e:
            return {
                'success': False,
                'exit_code': -1,
                'stdout': '',
                'stderr': f'命令执行错误: {str(e)}'
            }

    def _heartbeat_loop(self):
        """心跳检测线程：定期检查 SSH 连接"""
        while self._running:
            time.sleep(HEARTBEAT_INTERVAL)
            if not self._running:
                break
            with self._lock:
                if not self._is_ssh_alive():
                    print("[DAEMON] SSH 连接断开，尝试重连...", file=sys.stderr)
                    self._reconnect_ssh()

    def _idle_check_loop(self):
        """空闲检测线程：超时自动退出"""
        while self._running:
            time.sleep(10)
            if not self._running:
                break
            idle = time.time() - self._last_activity
            if idle >= self.idle_timeout:
                print(f"[DAEMON] 空闲 {int(idle)} 秒，自动退出", file=sys.stderr)
                self._running = False
                # 唤醒主循环的 accept
                try:
                    wake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wake.connect(('127.0.0.1', self._server_socket.getsockname()[1]))
                    wake.close()
                except Exception:
                    pass
                break

    def _shutdown(self):
        """清理并退出"""
        self._running = False

        # 关闭 TCP 服务
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        # 关闭 SSH 连接
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception:
                pass

        # 删除信息文件
        info_path = get_daemon_info_path(self.alias)
        try:
            if os.path.exists(info_path):
                os.remove(info_path)
        except Exception:
            pass

        print("[DAEMON] 已退出", file=sys.stderr)

    @property
    def _start_time(self):
        if not hasattr(self, '__start_time'):
            self.__start_time = time.time()
        return self.__start_time


# === CLI 入口 ===

def cmd_start(alias: str, idle_timeout: int = IDLE_TIMEOUT):
    """启动守护进程"""
    # 检查是否已有守护进程运行
    existing = read_daemon_info(alias)
    if existing:
        print(json.dumps({
            'status': 'already_running',
            'pid': existing['pid'],
            'port': existing['port'],
            'alias': alias,
            'host': existing.get('host', 'unknown')
        }, ensure_ascii=False))
        return

    daemon = SSHDaemon(alias, idle_timeout)
    daemon.start()


def cmd_stop(alias: str):
    """停止守护进程"""
    info = read_daemon_info(alias)
    if not info:
        print(json.dumps({'status': 'not_running'}, ensure_ascii=False))
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', info['port']))
        _send_message(sock, {'action': 'shutdown'})
        resp = _recv_message(sock, timeout=5)
        sock.close()
        print(json.dumps({'status': 'stopped', **resp}, ensure_ascii=False))
    except Exception as e:
        # 进程可能已死，清理信息文件
        info_path = get_daemon_info_path(alias)
        if os.path.exists(info_path):
            os.remove(info_path)
        print(json.dumps({'status': 'force_cleaned', 'error': str(e)}, ensure_ascii=False))


def cmd_status(alias: str):
    """查询守护进程状态"""
    info = read_daemon_info(alias)
    if not info:
        print(json.dumps({'status': 'not_running'}, ensure_ascii=False))
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', info['port']))
        _send_message(sock, {'action': 'ping'})
        resp = _recv_message(sock, timeout=5)
        sock.close()
        print(json.dumps({'status': 'running', **resp}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'status': 'unreachable', 'error': str(e)}, ensure_ascii=False))


def _legacy_main(argv=None):
    parser = argparse.ArgumentParser(description='SSH 长连接守护进程 v3.0')
    subparsers = parser.add_subparsers(dest='command', help='操作命令')

    # start
    p_start = subparsers.add_parser('start', help='启动守护进程')
    p_start.add_argument('alias', help='SSH host 别名（来自 ~/.ssh/config）')
    p_start.add_argument('--idle-timeout', type=int, default=IDLE_TIMEOUT,
                         help=f'空闲超时（秒），默认 {IDLE_TIMEOUT}')

    # stop
    p_stop = subparsers.add_parser('stop', help='停止守护进程')
    p_stop.add_argument('alias', help='SSH host 别名')

    # status
    p_status = subparsers.add_parser('status', help='查询守护进程状态')
    p_status.add_argument('alias', help='SSH host 别名')

    args = parser.parse_args(argv)

    if args.command == 'start':
        cmd_start(args.alias, args.idle_timeout)
    elif args.command == 'stop':
        cmd_stop(args.alias)
    elif args.command == 'status':
        cmd_status(args.alias)
    else:
        parser.print_help()
        sys.exit(1)


def main(argv=None):
    from ssh_skill import delegate_legacy_entrypoint

    arguments = sys.argv[1:] if argv is None else list(argv)
    return delegate_legacy_entrypoint(
        'daemon', arguments, legacy_main=_legacy_main
    )


if __name__ == '__main__':
    raise SystemExit(main())
