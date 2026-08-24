"""
工具函数模块

提供SSH相关的辅助工具函数。
"""

import os
import platform
import subprocess
from typing import Optional

from .platform_adapter import find_openssh, normalize_platform


def check_ssh_available() -> bool:
    """
    检查系统是否安装了SSH客户端

    Returns:
        安装了返回True，否则返回False
    """
    executable = find_openssh(normalize_platform())
    if not executable:
        return False
    try:
        subprocess.run(
            [executable, "-V"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_ssh_version() -> Optional[str]:
    """
    获取SSH客户端版本

    Returns:
        版本字符串，如果获取失败返回None
    """
    executable = find_openssh(normalize_platform())
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "-V"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        # SSH版本信息通常输出到stderr
        version_str = result.stderr.strip() if result.stderr else result.stdout.strip()
        return version_str
    except Exception:
        return None


def validate_key_file(key_file: str) -> tuple[bool, str]:
    """
    验证SSH密钥文件

    Args:
        key_file: 密钥文件路径

    Returns:
        (是否有效, 错误信息)元组
    """
    if not os.path.exists(key_file):
        return False, f"密钥文件不存在: {key_file}"

    if not os.path.isfile(key_file):
        return False, f"密钥路径不是文件: {key_file}"

    # 在Unix系统上检查文件权限
    if platform.system() != "Windows":
        stat_info = os.stat(key_file)
        mode = stat_info.st_mode & 0o777
        if mode & 0o077:  # 检查group和other权限
            return False, f"密钥文件权限过于宽松，应该设置为600: {key_file}"

    return True, ""


def format_ssh_command(host: str, user: str, command: str,
                      key_file: Optional[str] = None,
                      port: int = 22) -> str:
    """
    格式化SSH命令字符串（用于显示）

    Args:
        host: 服务器地址
        user: 用户名
        command: 要执行的命令
        key_file: 密钥文件路径
        port: 端口

    Returns:
        格式化的SSH命令字符串
    """
    parts = ["ssh"]

    if key_file:
        parts.extend(["-i", key_file])

    if port != 22:
        parts.extend(["-p", str(port)])

    parts.append(f"{user}@{host}")
    parts.append(f'"{command}"')

    return " ".join(parts)


def parse_ssh_output(output: str) -> dict:
    """
    解析SSH命令输出

    Args:
        output: SSH命令的输出

    Returns:
        解析后的字典
    """
    lines = output.strip().split('\n')
    return {
        'lines': lines,
        'line_count': len(lines),
        'first_line': lines[0] if lines else '',
        'last_line': lines[-1] if lines else ''
    }
