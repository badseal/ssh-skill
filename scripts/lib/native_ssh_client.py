"""
原生 SSH 客户端模块

使用原生 ssh/scp 命令，充分利用系统级 SSH 特性：
- ControlMaster 自动连接复用
- ProxyJump 原生跳板机支持
- ForwardAgent 完美支持
- 性能更好，功能更完整

适用场景：密钥认证（无密码）
"""

import subprocess
import os
import tempfile
from typing import Any, Dict, Optional, Iterator
from dataclasses import dataclass, field

from openssh_transport import OpenSSHOptions, run_openssh
from output_limits import collect_text
from platform_adapter import find_openssh, normalize_platform


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


class NativeSSHClient:
    """基于原生 SSH 命令的客户端

    使用系统原生 ssh/scp 命令，支持：
    - 密钥认证
    - ProxyJump 跳板机
    - ForwardAgent 代理转发
    - ControlMaster 连接复用
    """

    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_file: Optional[str] = None,
        timeout: int = 30,
        proxy_jump: Optional[str] = None,
        forward_agent: bool = False,
        alias: Optional[str] = None
    ):
        """
        初始化原生 SSH 客户端

        Args:
            host: SSH服务器地址
            user: SSH用户名
            port: SSH端口，默认22
            key_file: SSH私钥文件路径
            timeout: 连接超时时间（秒），默认30
            proxy_jump: ProxyJump 配置（如 "user@jumphost:port"）
            forward_agent: 是否启用 SSH agent forwarding
            alias: 服务器别名（用于 ControlMaster）
        """
        self.host = host
        self.user = user
        self.port = port
        self.key_file = key_file
        self.timeout = timeout
        self.proxy_jump = proxy_jump
        self.forward_agent = forward_agent
        self.alias = alias or f"{user}@{host}:{port}"

    def _build_ssh_base_args(self) -> list:
        """构建 SSH 基础参数"""
        args = [
            "ssh",
            "-p", str(self.port),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={self.timeout}",
        ]

        # 密钥文件
        if self.key_file:
            args.extend(["-i", os.path.expanduser(self.key_file)])

        # ProxyJump
        if self.proxy_jump:
            args.extend(["-o", f"ProxyJump={self.proxy_jump}"])

        # ForwardAgent
        if self.forward_agent:
            args.extend(["-o", "ForwardAgent=yes"])

        # ControlMaster（连接复用）
        # 注意：Windows + 跳板机场景下 ControlMaster 可能不稳定，暂时禁用
        # TODO: 后续优化 Windows 上的 ControlMaster 支持
        # control_path = self._get_control_path()
        # args.extend([
        #     "-o", "ControlMaster=auto",
        #     "-o", f"ControlPath={control_path}",
        #     "-o", "ControlPersist=600",  # 保持10分钟
        # ])

        return args

    def _get_control_path(self) -> str:
        """获取 ControlMaster socket 路径"""
        # 使用临时目录
        temp_dir = tempfile.gettempdir()
        # 使用别名作为标识，避免路径过长
        safe_alias = self.alias.replace('/', '_').replace('@', '_').replace(':', '_')
        return os.path.join(temp_dir, f"ssh-control-{safe_alias}")

    def execute(self, command: str) -> SSHResult:
        """
        执行SSH命令

        Args:
            command: 要执行的命令

        Returns:
            SSHResult对象，包含执行结果
        """
        executable = find_openssh(normalize_platform())
        if not executable:
            return SSHResult(
                success=False,
                stdout="",
                stderr="OpenSSH client was not found",
                exit_code=-1
            )
        target = self.alias if self.alias else f"{self.user}@{self.host}"
        result = run_openssh(
            OpenSSHOptions(
                executable=executable,
                config_path=os.path.expanduser("~/.ssh/config"),
                port=self.port,
                key_file=os.path.expanduser(self.key_file) if self.key_file else None,
                proxy_jump=self.proxy_jump,
                forward_agent=self.forward_agent,
            ),
            target,
            command,
            None,
            self.timeout,
        )
        return SSHResult(
            result.success,
            result.stdout,
            result.stderr,
            result.exit_code,
            output=result.output,
            error_code=result.error_code,
            retryable=result.retryable,
            outcome=result.outcome,
        )

    def upload(self, local_path: str, remote_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        上传文件到远程服务器

        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            timeout: 超时时间（秒），None 表示根据文件大小自动计算
            show_progress: 是否显示传输进度

        Returns:
            SSHResult对象，包含操作结果
        """
        # 检查本地文件是否存在
        if not os.path.exists(local_path):
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Local file not found: {local_path}",
                exit_code=-1
            )

        try:
            # 根据文件大小自动计算超时时间
            if timeout is None:
                file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
                # 假设最低速度 1MB/s，加上 60 秒缓冲时间
                calculated_timeout = int(file_size_mb / 1.0) + 60
                # 最小 60 秒，最大 3600 秒（1小时）
                actual_timeout = max(60, min(calculated_timeout, 3600))
            else:
                actual_timeout = timeout

            args = ["scp"]

            # 基本参数
            args.extend(["-P", str(self.port)])
            args.extend(["-o", "StrictHostKeyChecking=accept-new"])

            # 密钥文件
            if self.key_file:
                args.extend(["-i", os.path.expanduser(self.key_file)])

            # ProxyJump
            if self.proxy_jump:
                args.extend(["-o", f"ProxyJump={self.proxy_jump}"])

            # ControlMaster（复用 SSH 连接）
            # 注意：Windows + 跳板机场景下 ControlMaster 可能不稳定，暂时禁用
            # control_path = self._get_control_path()
            # args.extend([
            #     "-o", "ControlMaster=auto",
            #     "-o", f"ControlPath={control_path}",
            #     "-o", "ControlPersist=600",
            # ])

            # 源和目标
            args.append(local_path)
            args.append(f"{self.user}@{self.host}:{remote_path}")

            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=actual_timeout
            )

            return _bounded_ssh_result(
                result.returncode == 0,
                f"File uploaded: {local_path} -> {remote_path}" if result.returncode == 0 else result.stdout,
                result.stderr if result.returncode != 0 else "",
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Upload timeout after {timeout or self.timeout} seconds",
                exit_code=-1
            )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Upload error: {str(e)}",
                exit_code=-1
            )

    def download(self, remote_path: str, local_path: str, timeout: Optional[int] = None, show_progress: bool = True) -> SSHResult:
        """
        从远程服务器下载文件

        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径
            timeout: 超时时间（秒），None 表示根据文件大小自动计算
            show_progress: 是否显示传输进度

        Returns:
            SSHResult对象，包含操作结果
        """
        try:
            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir and not os.path.exists(local_dir):
                os.makedirs(local_dir, exist_ok=True)

            # 如果没有指定超时，使用默认值（下载时无法提前知道文件大小）
            actual_timeout = timeout if timeout is not None else 600  # 默认 10 分钟

            args = ["scp"]

            # 基本参数
            args.extend(["-P", str(self.port)])
            args.extend(["-o", "StrictHostKeyChecking=accept-new"])

            # 密钥文件
            if self.key_file:
                args.extend(["-i", os.path.expanduser(self.key_file)])

            # ProxyJump
            if self.proxy_jump:
                args.extend(["-o", f"ProxyJump={self.proxy_jump}"])

            # ControlMaster（复用 SSH 连接）
            # 注意：Windows + 跳板机场景下 ControlMaster 可能不稳定，暂时禁用
            # control_path = self._get_control_path()
            # args.extend([
            #     "-o", "ControlMaster=auto",
            #     "-o", f"ControlPath={control_path}",
            #     "-o", "ControlPersist=600",
            # ])

            # 源和目标
            args.append(f"{self.user}@{self.host}:{remote_path}")
            args.append(local_path)

            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=actual_timeout
            )

            return _bounded_ssh_result(
                result.returncode == 0,
                f"File downloaded: {remote_path} -> {local_path}" if result.returncode == 0 else result.stdout,
                result.stderr if result.returncode != 0 else "",
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Download timeout after {timeout or self.timeout} seconds",
                exit_code=-1
            )
        except Exception as e:
            return SSHResult(
                success=False,
                stdout="",
                stderr=f"Download error: {str(e)}",
                exit_code=-1
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
            args = self._build_ssh_base_args()
            args.append(f"{self.user}@{self.host}")
            args.append(command)

            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            # 逐行读取输出
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if not line:
                        break
                    yield line.rstrip('\n')

            # 等待进程结束
            process.wait(timeout=timeout or self.timeout)

            # 如果有错误输出，也返回
            if process.stderr:
                for line in process.stderr:
                    yield "[STDERR] " + line.rstrip("\n")

        except subprocess.TimeoutExpired:
            if process:
                process.kill()
            yield f"[ERROR] Command timeout after {timeout or self.timeout} seconds"
        except Exception as e:
            yield f"[ERROR] Execution error: {str(e)}"
