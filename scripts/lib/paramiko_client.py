"""
Paramiko SSH客户端模块

基于 paramiko 库实现密码认证和连接池管理，
为密码认证提供类似 ControlMaster 的连接复用功能。
"""

import paramiko
import threading
import time
import os
import re
import socket
from contextlib import nullcontext
from typing import Optional, List, Union, Dict, Iterator, Any
from dataclasses import dataclass, field
from io import StringIO

from output_limits import BoundedText, ProgressEmitter, collect_text
from platform_adapter import normalize_platform
from security import askpass_environment, configure_paramiko_host_keys


@dataclass
class SSHResult:
    """SSH命令执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    output: Dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool | None = None
    outcome: str | None = None


def _bounded_ssh_result(
    success: bool,
    stdout: str | bytes,
    stderr: str | bytes,
    exit_code: int,
    *,
    error_code: str | None = None,
    retryable: bool | None = None,
    outcome: str | None = None,
) -> SSHResult:
    bounded_stdout = collect_text(stdout)
    bounded_stderr = collect_text(stderr)
    return SSHResult(
        success=success,
        stdout=bounded_stdout.text,
        stderr=bounded_stderr.text,
        exit_code=exit_code,
        output={
            "stdout": bounded_stdout.to_meta(),
            "stderr": bounded_stderr.to_meta(),
        },
        error_code=error_code,
        retryable=retryable,
        outcome=outcome,
    )


class ConnectionPool:
    """SSH 连接池管理器

    实现连接复用，为密码认证提供类似 ControlMaster 的功能。
    """

    def __init__(self, max_idle_time: int = 600):
        """
        初始化连接池

        Args:
            max_idle_time: 最大空闲时间（秒），默认 600秒（10分钟）
        """
        self._pool = {}  # {connection_key: (ssh_client, last_used_time)}
        self._lock = threading.Lock()
        self._max_idle_time = max_idle_time

    def _get_key(self, host: str, port: int, user: str) -> str:
        """生成连接唯一标识"""
        return f"{user}@{host}:{port}"

    def get_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
        key_passphrase: Optional[str] = None,
        timeout: int = 30
    ) -> paramiko.SSHClient:
        """
        获取连接（从池中复用或创建新连接）

        Args:
            host: 主机地址
            port: 端口
            user: 用户名
            password: 密码（密码认证）
            key_file: 密钥文件（密钥认证）
            key_passphrase: 密钥密码
            timeout: 超时时间

        Returns:
            paramiko.SSHClient 对象
        """
        key = self._get_key(host, port, user)

        with self._lock:
            # 清理过期连接
            self._cleanup_idle_connections()

            # 尝试复用现有连接
            if key in self._pool:
                client, last_used = self._pool[key]
                # 检查连接是否仍然有效
                if self._is_connection_alive(client):
                    self._pool[key] = (client, time.time())
                    return client
                else:
                    # 连接已断开，移除
                    try:
                        client.close()
                    except:
                        pass
                    del self._pool[key]

            # 创建新连接
            client = paramiko.SSHClient()
            configure_paramiko_host_keys(client)

            try:
                if password:
                    # 密码认证
                    client.connect(
                        hostname=host,
                        port=port,
                        username=user,
                        password=password,
                        timeout=timeout,
                        look_for_keys=False,
                        allow_agent=False
                    )
                elif key_file:
                    # 密钥认证 - 优先尝试 ssh-agent
                    client.connect(
                        hostname=host,
                        port=port,
                        username=user,
                        key_filename=key_file,
                        timeout=timeout,
                        look_for_keys=True,  # 允许查找密钥
                        allow_agent=True     # 允许使用 ssh-agent
                    )
                else:
                    # 无密码无密钥 - 尝试 ssh-agent
                    client.connect(
                        hostname=host,
                        port=port,
                        username=user,
                        timeout=timeout,
                        look_for_keys=True,
                        allow_agent=True
                    )

                # 加入连接池
                self._pool[key] = (client, time.time())
                return client

            except Exception as e:
                try:
                    client.close()
                except:
                    pass
                raise

    def _is_connection_alive(self, client: paramiko.SSHClient) -> bool:
        """检查连接是否仍然有效"""
        try:
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                return False
            # 发送一个简单的命令测试连接
            transport.send_ignore()
            return True
        except:
            return False

    def _cleanup_idle_connections(self):
        """清理空闲超时的连接"""
        current_time = time.time()
        keys_to_remove = []

        for key, (client, last_used) in self._pool.items():
            if current_time - last_used > self._max_idle_time:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            client, _ = self._pool[key]
            try:
                client.close()
            except:
                pass
            del self._pool[key]

    def close_all(self):
        """关闭所有连接"""
        with self._lock:
            for client, _ in self._pool.values():
                try:
                    client.close()
                except:
                    pass
            self._pool.clear()


# 全局连接池实例
_connection_pool = ConnectionPool()


class ParamikoClient:
    """基于 Paramiko 的 SSH 客户端

    支持密码认证和连接池管理。
    提供与 SSHClient 相同的接口。
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
        port: int = 22,
        timeout: int = 30,
        key_passphrase: Optional[str] = None,
        jump_hosts: Optional[List[Union[str, Dict]]] = None,
        forward_agent: bool = False,
        transfer_timeout: Optional[int] = None
    ):
        """
        初始化 Paramiko SSH 客户端

        Args:
            host: SSH服务器地址
            user: SSH用户名
            password: 密码（密码认证）
            key_file: SSH私钥文件路径（密钥认证）
            port: SSH端口，默认22
            timeout: 连接超时时间（秒），默认30
            key_passphrase: SSH私钥密码（如果私钥有密码保护）
            jump_hosts: 跳板机列表
            forward_agent: 是否启用 SSH agent forwarding
            transfer_timeout: 文件传输超时时间（秒），None 表示无限制（推荐用于大文件）
        """
        self.host = host
        self.user = user
        self.password = password
        self.key_file = key_file
        self.port = port
        self.timeout = timeout
        self.key_passphrase = key_passphrase
        self.jump_hosts = jump_hosts or []
        self.forward_agent = forward_agent
        self.transfer_timeout = transfer_timeout  # 文件传输超时（None表示无限制）
        self._jump_clients = []  # 保存跳板机连接链

        # 验证认证方式
        if not password and not key_file:
            raise ValueError("必须提供 password 或 key_file")

        # Performance warning: Password auth + jump hosts has lower performance
        if self.password and self.jump_hosts:
            import sys
            print(
                "[INFO] Password auth + jump hosts mode detected.\n"
                "File transfers will use scp command (slower but functional).\n"
                "Recommended: Upgrade to key-based auth for better performance.",
                file=sys.stderr
            )

    def _build_jump_string(self) -> Optional[str]:
        """
        构建 ProxyJump 参数字符串

        Returns:
            ProxyJump 字符串，如果没有跳板机返回 None
        """
        if not self.jump_hosts:
            return None

        jump_parts = []
        for jump in self.jump_hosts:
            if isinstance(jump, str):
                # 简化格式：只有主机名
                jump_parts.append(jump)
            elif isinstance(jump, dict):
                # 完整格式：包含用户名、主机、端口等
                host = jump['host']
                user = jump.get('user', self.user)
                port = jump.get('port', 22)

                if port != 22:
                    jump_parts.append(f"{user}@{host}:{port}")
                else:
                    jump_parts.append(f"{user}@{host}")

        return ','.join(jump_parts) if jump_parts else None

    def _build_scp_command(self, source: str, destination: str, upload: bool = True) -> List[str]:
        """
        构建 scp 命令（用于跳板机场景的文件传输）

        Args:
            source: 源文件路径
            destination: 目标文件路径
            upload: True 表示上传，False 表示下载

        Returns:
            scp 命令列表
        """
        def _escape_scp_path(path: str) -> str:
            """转义 SCP 路径中的特殊字符"""
            if any(c in path for c in [' ', "'", '"', '$', '`']):
                path = path.replace(' ', '\\ ')
                path = path.replace("'", "\\'")
                path = path.replace('"', '\\"')
                path = path.replace('$', '\\$')
                path = path.replace('`', '\\`')
            return path

        cmd = ["scp"]

        # 基本参数
        cmd.extend(["-P", str(self.port)])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])

        # ProxyJump 支持
        jump_string = self._build_jump_string()
        if jump_string:
            cmd.extend(["-o", f"ProxyJump={jump_string}"])

        # 源和目标
        if upload:
            escaped_dest = _escape_scp_path(destination)
            remote_dest = f"{self.user}@{self.host}:{escaped_dest}"
            cmd.extend([source, remote_dest])
        else:
            escaped_source = _escape_scp_path(source)
            remote_source = f"{self.user}@{self.host}:{escaped_source}"
            cmd.extend([remote_source, destination])

        return cmd

    def _scp_environment(self):
        if self.password:
            return askpass_environment(self.password, normalize_platform())
        return nullcontext(os.environ.copy())

    def _run_scp_command(
        self,
        scp_cmd: List[str],
        *,
        operation: str,
        success_message: str,
        timeout: Optional[int],
        show_progress: bool,
    ) -> SSHResult:
        import subprocess
        import sys

        process = None
        stderr_collector = BoundedText()
        progress_emitter = ProgressEmitter(sys.stderr, enabled=show_progress)
        try:
            with self._scp_environment() as environment:
                process = subprocess.Popen(
                    scp_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    env=environment,
                )

                if show_progress and process.stderr:
                    for line in iter(process.stderr.readline, ''):
                        if not line:
                            break
                        stderr_collector.feed(line.encode('utf-8'))
                        match = re.search(r'(\d+)%', line)
                        if match:
                            percent = min(100, int(match.group(1)))
                            progress_emitter.emit(operation, percent, 100)

                stdout, remaining_stderr = process.communicate(timeout=timeout)
                if remaining_stderr:
                    stderr_collector.feed(remaining_stderr.encode('utf-8'))

            stdout_result = collect_text(stdout or success_message)
            stderr_result = stderr_collector.finish()
            return SSHResult(
                success=process.returncode == 0,
                stdout=stdout_result.text,
                stderr=stderr_result.text if process.returncode != 0 else "",
                exit_code=process.returncode,
                output={
                    'stdout': stdout_result.to_meta(),
                    'stderr': stderr_result.to_meta(),
                },
            )
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
            return _bounded_ssh_result(
                False, "", f"{operation.title()} timeout after {timeout} seconds", -1
            )
        except Exception as exc:
            return _bounded_ssh_result(
                False, "", f"{operation.title()} error: {exc}", -1
            )

    def _connect_through_jump_hosts(self) -> paramiko.SSHClient:
        """
        通过跳板机链连接到目标服务器（不使用连接池）

        注意：此方法为每次连接创建新的 SSH 链路，性能较低。
        密码认证 + 跳板机无法使用连接复用。

        Returns:
            连接到目标服务器的 paramiko.SSHClient
        """
        # 清理之前的跳板机连接
        self._cleanup_jump_connections()

        try:
            # 1. 连接到第一个跳板机
            current_client = paramiko.SSHClient()
            configure_paramiko_host_keys(current_client)

            # 解析第一个跳板机配置
            first_jump = self.jump_hosts[0]
            if isinstance(first_jump, str):
                # 简化格式: "user@host" 或 "user@host:port"
                jump_parts = first_jump.replace('@', ' ').replace(':', ' ').split()
                jump_user = jump_parts[0] if len(jump_parts) > 0 else self.user
                jump_host = jump_parts[1] if len(jump_parts) > 1 else first_jump
                jump_port = int(jump_parts[2]) if len(jump_parts) > 2 else 22
                jump_password = self.password  # 使用相同的密码
                jump_key_file = None
            else:
                # 字典格式
                jump_host = first_jump.get('host')
                jump_user = first_jump.get('user', self.user)
                jump_port = first_jump.get('port', 22)
                jump_password = first_jump.get('password', self.password)
                jump_key_file = first_jump.get('key_file')

            # 连接到第一个跳板机
            if jump_password:
                current_client.connect(
                    hostname=jump_host,
                    port=jump_port,
                    username=jump_user,
                    password=jump_password,
                    timeout=self.timeout,
                    look_for_keys=False,
                    allow_agent=False
                )
            elif jump_key_file:
                pkey = paramiko.RSAKey.from_private_key_file(jump_key_file)
                current_client.connect(
                    hostname=jump_host,
                    port=jump_port,
                    username=jump_user,
                    pkey=pkey,
                    timeout=self.timeout
                )
            else:
                raise ValueError(f"跳板机 {jump_host} 必须提供 password 或 key_file")

            self._jump_clients.append(current_client)

            # 2. 依次通过剩余的跳板机
            for jump in self.jump_hosts[1:]:
                # 解析跳板机配置
                if isinstance(jump, str):
                    jump_parts = jump.replace('@', ' ').replace(':', ' ').split()
                    jump_user = jump_parts[0] if len(jump_parts) > 0 else self.user
                    jump_host = jump_parts[1] if len(jump_parts) > 1 else jump
                    jump_port = int(jump_parts[2]) if len(jump_parts) > 2 else 22
                    jump_password = self.password
                    jump_key_file = None
                else:
                    jump_host = jump.get('host')
                    jump_user = jump.get('user', self.user)
                    jump_port = jump.get('port', 22)
                    jump_password = jump.get('password', self.password)
                    jump_key_file = jump.get('key_file')

                # 通过当前跳板机创建到下一个跳板机的通道
                transport = current_client.get_transport()
                dest_addr = (jump_host, jump_port)
                local_addr = ('127.0.0.1', 0)
                channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

                # 通过通道连接到下一个跳板机
                next_client = paramiko.SSHClient()
                configure_paramiko_host_keys(next_client)

                if jump_password:
                    next_client.connect(
                        hostname=jump_host,
                        port=jump_port,
                        username=jump_user,
                        password=jump_password,
                        sock=channel,
                        timeout=self.timeout,
                        look_for_keys=False,
                        allow_agent=False
                    )
                elif jump_key_file:
                    pkey = paramiko.RSAKey.from_private_key_file(jump_key_file)
                    next_client.connect(
                        hostname=jump_host,
                        port=jump_port,
                        username=jump_user,
                        pkey=pkey,
                        sock=channel,
                        timeout=self.timeout
                    )
                else:
                    raise ValueError(f"跳板机 {jump_host} 必须提供 password 或 key_file")

                self._jump_clients.append(next_client)
                current_client = next_client

            # 3. 通过最后一个跳板机连接到目标服务器
            transport = current_client.get_transport()
            dest_addr = (self.host, self.port)
            local_addr = ('127.0.0.1', 0)
            channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

            # 连接到目标服务器
            target_client = paramiko.SSHClient()
            configure_paramiko_host_keys(target_client)

            if self.password:
                target_client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    sock=channel,
                    timeout=self.timeout,
                    look_for_keys=False,
                    allow_agent=False
                )
            elif self.key_file:
                pkey = None
                if self.key_passphrase:
                    pkey = paramiko.RSAKey.from_private_key_file(self.key_file, password=self.key_passphrase)
                else:
                    pkey = paramiko.RSAKey.from_private_key_file(self.key_file)

                target_client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    pkey=pkey,
                    sock=channel,
                    timeout=self.timeout
                )
            else:
                raise ValueError("目标服务器必须提供 password 或 key_file")

            return target_client

        except Exception as e:
            # Clean up all jump host connections on error
            self._cleanup_jump_connections()
            raise Exception(f"Jump host connection failed: {str(e)}")

    def _cleanup_jump_connections(self):
        """Clean up jump host connection chain"""
        for client in self._jump_clients:
            try:
                client.close()
            except:
                pass
        self._jump_clients.clear()

    def _get_connection(self) -> paramiko.SSHClient:
        """获取连接（使用连接池或直接连接）"""
        # 如果有跳板机，使用直接连接（不使用连接池）
        if self.jump_hosts:
            return self._connect_through_jump_hosts()

        # 无跳板机，使用连接池
        return _connection_pool.get_connection(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            key_file=self.key_file,
            key_passphrase=self.key_passphrase,
            timeout=self.timeout
        )

    def execute(self, command: str) -> SSHResult:
        """
        执行SSH命令

        Args:
            command: 要执行的命令

        Returns:
            SSHResult对象，包含执行结果
        """
        try:
            client = self._get_connection()
            stdin, stdout, stderr = client.exec_command(command, timeout=self.timeout)

            stdout_bytes = stdout.read()
            stderr_bytes = stderr.read()
            exit_code = stdout.channel.recv_exit_status()

            return _bounded_ssh_result(
                exit_code == 0,
                stdout_bytes,
                stderr_bytes,
                exit_code,
            )
        except (TimeoutError, socket.timeout):
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Execution timeout after {self.timeout} seconds",
                exit_code=-1,
                error_code="outcome_unknown",
                retryable=False,
                outcome="unknown",
            )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Execution error: {str(e)}",
                exit_code=-1
            )

    def execute_with_agent_forward(self, command: str, timeout: Optional[int] = None) -> SSHResult:
        """
        执行命令并启用 SSH agent forwarding

        用于服务器间传输场景：在源服务器上执行 scp/rsync 命令，
        通过 agent forwarding 让源服务器使用本地的 SSH 密钥认证到目标服务器。

        Args:
            command: 要执行的命令
            timeout: 超时时间（秒）

        Returns:
            SSHResult对象
        """
        cmd_timeout = timeout or self.timeout

        try:
            # 创建新连接（启用 agent）
            client = paramiko.SSHClient()
            configure_paramiko_host_keys(client)

            connect_kwargs = {
                'hostname': self.host,
                'port': self.port,
                'username': self.user,
                'timeout': cmd_timeout,
                'allow_agent': True,
                'look_for_keys': True,
            }
            if self.key_file:
                connect_kwargs['key_filename'] = self.key_file
            if self.password:
                connect_kwargs['password'] = self.password

            client.connect(**connect_kwargs)

            # 启用 agent forwarding
            transport = client.get_transport()
            session = transport.open_session()
            try:
                paramiko.agent.AgentRequestHandler(session)
            except Exception:
                pass  # agent forwarding 不可用时继续

            # 执行命令（使用 PTY）
            stdin, stdout, stderr = client.exec_command(
                command, timeout=cmd_timeout, get_pty=True
            )

            stdout_bytes = stdout.read()
            stderr_bytes = stderr.read()
            exit_code = stdout.channel.recv_exit_status()

            try:
                session.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

            return _bounded_ssh_result(
                exit_code == 0,
                stdout_bytes,
                stderr_bytes,
                exit_code,
            )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Agent forward execution error: {str(e)}",
                exit_code=-1
            )

    def upload(self, local_path: str, remote_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        上传文件到远程服务器（支持进度显示和大文件传输）

        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            timeout: 超时时间（秒），None 表示使用 transfer_timeout 或无限制
            show_progress: 是否显示传输进度

        Returns:
            SSHResult对象，包含操作结果
        """
        import os
        import sys

        # 检查本地文件是否存在
        if not os.path.exists(local_path):
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Local file not found: {local_path}",
                exit_code=-1
            )

        # 如果有跳板机，使用 Paramiko 通过跳板机连接传输文件
        if self.jump_hosts:
            return self._upload_via_jumphost(local_path, remote_path, show_progress)

        # 无跳板机，使用 Paramiko SFTP（连接池）+ SFTPTransfer（支持进度）
        try:
            from sftp_transfer import SFTPTransfer, TransferProgress

            client = self._get_connection()
            sftp = client.open_sftp()

            # 设置 SFTP 超时（如果指定）
            actual_timeout = timeout if timeout is not None else self.transfer_timeout
            if actual_timeout:
                sftp.get_channel().settimeout(actual_timeout)
            else:
                # 大文件传输：设置为 None（无限制）
                sftp.get_channel().settimeout(None)

            progress_emitter = ProgressEmitter(sys.stderr, enabled=show_progress)

            def progress_callback(progress: TransferProgress):
                progress_emitter.emit(
                    'upload', progress.transferred_bytes, progress.total_bytes,
                    file_path=progress.file_path,
                )

            # 使用 SFTPTransfer 上传（支持分块和进度）
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback if show_progress else None)
            result = transfer.upload_file(local_path, remote_path, resume=False)

            sftp.close()

            if result.success:
                return SSHResult(
                    success=True,
                    stdout=f"File uploaded: {local_path} -> {remote_path} ({result.details[0].get('speed', 'N/A')})",
                    stderr="",
                    exit_code=0
                )
            else:
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=f"Upload error: {'; '.join(result.errors)}",
                    exit_code=-1
                )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Upload error: {str(e)}",
                exit_code=-1
            )

    def _upload_via_jumphost(self, local_path: str, remote_path: str, show_progress: bool = True) -> SSHResult:
        """
        通过 Paramiko 跳板机连接上传文件（支持进度显示）

        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            show_progress: 是否显示进度

        Returns:
            SSHResult对象
        """
        import sys

        try:
            from sftp_transfer import SFTPTransfer, TransferProgress

            # 转换为绝对路径
            local_path = os.path.abspath(local_path)

            # 检查本地文件是否存在
            if not os.path.isfile(local_path):
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=f"本地文件不存在: {local_path}",
                    exit_code=-1
                )

            # 使用跳板机连接（会创建完整的连接链）
            client = self._connect_through_jump_hosts()

            # 在跳板机连接上打开 SFTP
            sftp = client.open_sftp()

            # 设置超时（大文件传输使用无限制）
            if self.transfer_timeout:
                sftp.get_channel().settimeout(self.transfer_timeout)
            else:
                sftp.get_channel().settimeout(None)

            progress_emitter = ProgressEmitter(sys.stderr, enabled=show_progress)

            def progress_callback(progress: TransferProgress):
                progress_emitter.emit(
                    'upload', progress.transferred_bytes, progress.total_bytes,
                    file_path=progress.file_path,
                )

            # 使用 SFTPTransfer 上传
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback if show_progress else None)
            result = transfer.upload_file(local_path, remote_path, resume=False)

            sftp.close()

            if result.success:
                return SSHResult(
                    success=True,
                    stdout=f"File uploaded via jump host: {local_path} -> {remote_path} ({result.details[0].get('speed', 'N/A')})",
                    stderr="",
                    exit_code=0
                )
            else:
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=f"Upload via jump host error: {'; '.join(result.errors)}",
                    exit_code=-1
                )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Upload via jump host error: {str(e)}",
                exit_code=-1
            )
        finally:
            # 清理跳板机连接
            self._cleanup_jump_connections()

    def _upload_via_scp(self, local_path: str, remote_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        通过 scp 命令上传文件（跳板机场景）

        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            timeout: 超时时间（秒）
            show_progress: 是否显示进度

        Returns:
            SSHResult对象
        """
        scp_cmd = self._build_scp_command(local_path, remote_path, upload=True)
        return self._run_scp_command(
            scp_cmd,
            operation='upload',
            success_message=f"File uploaded via scp: {local_path} -> {remote_path}",
            timeout=timeout,
            show_progress=show_progress,
        )

    def download(self, remote_path: str, local_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        从远程服务器下载文件（支持进度显示和大文件传输）

        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径
            timeout: 超时时间（秒），None 表示使用 transfer_timeout 或无限制
            show_progress: 是否显示传输进度

        Returns:
            SSHResult对象，包含操作结果
        """
        import sys

        # 如果有跳板机，使用 Paramiko 通过跳板机连接传输文件
        if self.jump_hosts:
            return self._download_via_jumphost(remote_path, local_path, show_progress)

        # 无跳板机，使用 Paramiko SFTP（连接池）+ SFTPTransfer（支持进度）
        try:
            from sftp_transfer import SFTPTransfer, TransferProgress

            client = self._get_connection()
            sftp = client.open_sftp()

            # 设置 SFTP 超时（如果指定）
            actual_timeout = timeout if timeout is not None else self.transfer_timeout
            if actual_timeout:
                sftp.get_channel().settimeout(actual_timeout)
            else:
                # 大文件传输：设置为 None（无限制）
                sftp.get_channel().settimeout(None)

            progress_emitter = ProgressEmitter(sys.stderr, enabled=show_progress)

            def progress_callback(progress: TransferProgress):
                progress_emitter.emit(
                    'download', progress.transferred_bytes, progress.total_bytes,
                    file_path=progress.file_path,
                )

            # 使用 SFTPTransfer 下载（支持分块和进度）
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback if show_progress else None)
            result = transfer.download_file(remote_path, local_path, resume=False)

            sftp.close()

            if result.success:
                return SSHResult(
                    success=True,
                    stdout=f"File downloaded: {remote_path} -> {local_path} ({result.details[0].get('speed', 'N/A')})",
                    stderr="",
                    exit_code=0
                )
            else:
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=f"Download error: {'; '.join(result.errors)}",
                    exit_code=-1
                )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Download error: {str(e)}",
                exit_code=-1
            )

    def _download_via_jumphost(self, remote_path: str, local_path: str, show_progress: bool = True) -> SSHResult:
        """
        通过 Paramiko 跳板机连接下载文件（支持进度显示）

        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径
            show_progress: 是否显示进度

        Returns:
            SSHResult对象
        """
        import sys

        try:
            from sftp_transfer import SFTPTransfer, TransferProgress

            # 转换为绝对路径
            local_path = os.path.abspath(local_path)

            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir and not os.path.exists(local_dir):
                os.makedirs(local_dir, exist_ok=True)

            # 使用跳板机连接（会创建完整的连接链）
            client = self._connect_through_jump_hosts()

            # 在跳板机连接上打开 SFTP
            sftp = client.open_sftp()

            # 设置超时（大文件传输使用无限制）
            if self.transfer_timeout:
                sftp.get_channel().settimeout(self.transfer_timeout)
            else:
                sftp.get_channel().settimeout(None)

            progress_emitter = ProgressEmitter(sys.stderr, enabled=show_progress)

            def progress_callback(progress: TransferProgress):
                progress_emitter.emit(
                    'download', progress.transferred_bytes, progress.total_bytes,
                    file_path=progress.file_path,
                )

            # 使用 SFTPTransfer 下载
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback if show_progress else None)
            result = transfer.download_file(remote_path, local_path, resume=False)

            sftp.close()

            if result.success:
                return SSHResult(
                    success=True,
                    stdout=f"File downloaded via jump host: {remote_path} -> {local_path} ({result.details[0].get('speed', 'N/A')})",
                    stderr="",
                    exit_code=0
                )
            else:
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=f"Download via jump host error: {'; '.join(result.errors)}",
                    exit_code=-1
                )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Download via jump host error: {str(e)}",
                exit_code=-1
            )
        finally:
            # 清理跳板机连接
            self._cleanup_jump_connections()

    def _download_via_scp(self, remote_path: str, local_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        通过 scp 命令下载文件（跳板机场景）

        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径
            timeout: 超时时间（秒）
            show_progress: 是否显示进度

        Returns:
            SSHResult对象
        """
        scp_cmd = self._build_scp_command(remote_path, local_path, upload=False)
        return self._run_scp_command(
            scp_cmd,
            operation='download',
            success_message=f"File downloaded via scp: {remote_path} -> {local_path}",
            timeout=timeout,
            show_progress=show_progress,
        )

    def test_connection(self) -> SSHResult:
        """
        测试SSH连接

        Returns:
            SSHResult对象，包含测试结果
        """
        return self.execute("echo 'Connection OK'")

    def execute_stream(self, command: str, timeout: Optional[int] = None) -> Iterator[str]:
        """
        实时流式执行命令，逐行返回输出

        Args:
            command: 要执行的命令
            timeout: 总超时时间（秒），默认使用实例的timeout

        Yields:
            命令输出的每一行
        """
        try:
            client = self._get_connection()
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout or self.timeout)

            # 逐行读取输出
            for line in stdout:
                yield line.rstrip('\n')

            # 如果有错误输出，也返回
            for line in stderr:
                yield "[STDERR] " + line.rstrip("\n")

        except Exception as e:
            yield f"[ERROR] Execution error: {str(e)}"
