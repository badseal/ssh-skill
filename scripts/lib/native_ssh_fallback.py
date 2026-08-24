"""
原生 SSH 降级模块

当检测到复杂场景（ProxyCommand、passphrase 等）时，
降级使用原生 ssh 命令而非 Paramiko。
"""

import subprocess
import os
import ntpath
from typing import Optional, Dict, Tuple

from openssh_transport import OpenSSHOptions, run_openssh
from platform_adapter import find_openssh, inspect_ssh_agent, normalize_platform


def _get_windows_native_ssh_path(exe_name: str = 'ssh.exe') -> Optional[str]:
    """
    获取 Windows 原生 OpenSSH 可执行文件的完整路径

    通过 %SystemRoot% 环境变量动态定位 System32\\OpenSSH 目录，
    而非依赖 PATH 查找。这样可以避免 Git Bash 的 ssh.exe 优先级
    高于 Windows 原生版本的问题。

    Git 的 ssh.exe 无法访问 Windows SSH Agent 服务，必须使用原生版本。

    Args:
        exe_name: 可执行文件名，如 'ssh.exe'、'ssh-add.exe'

    Returns:
        完整路径字符串，不存在则返回 None
    """
    if os.name != 'nt':
        return None
    system_root = os.environ.get('SystemRoot') or os.environ.get('WINDIR') or r'C:\Windows'
    exe_path = ntpath.join(system_root, 'System32', 'OpenSSH', exe_name)
    if os.path.isfile(exe_path):
        return exe_path
    return None


def check_windows_ssh_availability() -> Tuple[bool, str]:
    """
    检查 Windows 原生 OpenSSH 客户端是否可用

    Returns:
        (is_available, message_or_path) 元组
        - 可用时：(True, ssh.exe 完整路径)
        - 不可用时：(False, 错误信息)
    """
    if os.name != 'nt':
        return False, "非 Windows 系统"

    ssh_path = _get_windows_native_ssh_path('ssh.exe')
    if not ssh_path:
        return False, "未安装 Windows 原生 OpenSSH 客户端（System32\\OpenSSH\\ssh.exe 不存在）"

    return True, ssh_path


def should_use_native_ssh(ssh_config: dict, metadata: dict = None) -> Tuple[bool, str]:
    """
    检测是否应该使用原生 SSH 而非 Paramiko

    Args:
        ssh_config: SSH 配置字典（从 paramiko.SSHConfig.lookup 获取）
        metadata: 元数据字典（可选）

    Returns:
        (should_fallback, reason) 元组
    """
    reasons = []

    # 检测 ProxyCommand（包括 Cloudflare Tunnel）
    proxy_command = ssh_config.get('proxycommand')
    if proxy_command:
        # Cloudflare Tunnel
        if 'cloudflared' in proxy_command.lower():
            reasons.append("检测到 Cloudflare Tunnel (ProxyCommand)")
        # 其他 ProxyCommand
        else:
            reasons.append(f"检测到 ProxyCommand: {proxy_command}")

    # 检测 ProxyJump（多级跳板机）
    proxy_jump = ssh_config.get('proxyjump')
    if proxy_jump and ',' in proxy_jump:
        # 多级跳板机（单级跳板机 Paramiko 可以处理）
        reasons.append(f"检测到多级跳板机: {proxy_jump}")

    # 检测密钥文件是否需要 passphrase
    identity_file = ssh_config.get('identityfile')
    if identity_file:
        # 如果是列表，取第一个
        if isinstance(identity_file, list):
            identity_file = identity_file[0] if identity_file else None

        if identity_file and _key_has_passphrase(identity_file):
            reasons.append("检测到密钥需要 passphrase（建议使用 ssh-agent）")

    # 检测其他复杂配置
    if ssh_config.get('localforward') or ssh_config.get('remoteforward'):
        reasons.append("检测到端口转发配置")

    if ssh_config.get('dynamicforward'):
        reasons.append("检测到动态端口转发（SOCKS 代理）")

    # 如果有任何复杂场景，建议降级
    if reasons:
        return True, "; ".join(reasons)

    return False, ""


def _key_has_passphrase(key_file: str) -> bool:
    """
    检测密钥文件是否有 passphrase 保护

    注意：这是一个启发式检测，不是 100% 准确
    """
    try:
        key_file = os.path.expanduser(key_file)
        if not os.path.exists(key_file):
            return False

        with open(key_file, 'r') as f:
            content = f.read()

        # 检测加密标记（旧格式）
        if 'ENCRYPTED' in content:
            return True

        # OpenSSH 新格式的加密密钥
        if 'BEGIN OPENSSH PRIVATE KEY' in content:
            # 提取所有 base64 行（排除 BEGIN/END 行）
            lines = content.strip().split('\n')
            base64_lines = [line for line in lines
                           if line and not line.startswith('-----')]

            if base64_lines:
                try:
                    import base64
                    # 合并所有 base64 行后解码
                    base64_content = ''.join(base64_lines)
                    decoded = base64.b64decode(base64_content).decode('latin-1', errors='ignore')

                    # 检查是否包含加密算法标记
                    # 如果包含 'none' 且没有其他加密算法，表示未加密
                    has_encryption = any(marker in decoded for marker in
                                       ['aes128-ctr', 'aes192-ctr', 'aes256-ctr',
                                        'aes128-cbc', 'aes192-cbc', 'aes256-cbc'])

                    if has_encryption:
                        return True

                    # 如果只有 'none'，表示未加密
                    if 'none' in decoded and not has_encryption:
                        return False

                except Exception:
                    pass

        return False
    except Exception:
        return False


def execute_native_ssh(
    alias: str,
    command: str,
    timeout: int = 120,
    ssh_config_path: Optional[str] = None
) -> Dict:
    """
    使用原生 ssh 命令执行远程命令

    所有平台均以参数数组直接执行 OpenSSH。

    Args:
        alias: SSH 别名
        command: 要执行的命令
        timeout: 超时时间（秒）
        ssh_config_path: SSH 配置文件路径（默认 ~/.ssh/config）

    Returns:
        结果字典 {success, exit_code, stdout, stderr}
    """
    if ssh_config_path is None:
        ssh_config_path = os.path.expanduser("~/.ssh/config")

    platform_name = normalize_platform()
    executable = find_openssh(platform_name)
    if not executable:
        return {
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': 'OpenSSH client was not found',
            'method': f'native_ssh_{platform_name}'
        }

    result = run_openssh(
        OpenSSHOptions(executable=executable, config_path=ssh_config_path),
        alias,
        command,
        None,
        timeout,
    )
    return {
        'success': result.success,
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'method': f'native_ssh_{platform_name}',
        'output': result.output,
        'warnings': result.warnings,
        'error_code': result.error_code,
        'retryable': result.retryable,
        'outcome': result.outcome,
    }


def check_ssh_agent() -> Tuple[bool, str]:
    """
    检查 ssh-agent 是否运行且有密钥

    Returns:
        (is_available, message) 元组
    """
    status = inspect_ssh_agent(normalize_platform())
    return status.available, status.message
