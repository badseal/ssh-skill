"""
SSH Skill - 系统级SSH连接管理工具

基于系统OpenSSH的SSH客户端，提供稳定可靠的SSH连接能力。

核心特性:
- ControlMaster 连接复用（10-100x性能提升）
- ProxyJump 跳板机支持（支持多级）
- 项目级配置管理
- 流式输出和交互式会话
- 批量并发操作
"""

from .config_v3 import SSHConfigLoaderV3
from .native_ssh_client import NativeSSHClient, SSHResult
from .paramiko_client import ParamikoClient
from .cluster import SSHCluster
from .sftp_transfer import SFTPTransfer
from .utils import (
    check_ssh_available,
    get_ssh_version,
    validate_key_file
)

__version__ = "0.1.0"

__all__ = [
    "SSHConfigLoaderV3",
    "NativeSSHClient",
    "ParamikoClient",
    "SSHResult",
    "SSHCluster",
    "SFTPTransfer",
    "check_ssh_available",
    "get_ssh_version",
    "validate_key_file",
]
