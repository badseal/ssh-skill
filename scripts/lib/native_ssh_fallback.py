"""
原生 SSH 降级模块

当检测到复杂场景（ProxyCommand、passphrase 等）时，
降级使用原生 ssh 命令而非 Paramiko。
"""

import subprocess
import os
import json
from typing import Optional, Dict, Tuple


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
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    exe_path = os.path.join(system_root, 'System32', 'OpenSSH', exe_name)
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

    Windows 平台：通过 PowerShell 执行 SSH（以访问 Windows SSH Agent）
    Unix/Linux：直接执行 SSH

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

    # Windows 平台：通过 PowerShell 执行 SSH（才能访问 Windows SSH Agent）
    if os.name == 'nt':
        # 检查 OpenSSH 是否可用
        ssh_available, ssh_msg = check_windows_ssh_availability()
        if not ssh_available:
            return {
                'success': False,
                'exit_code': -1,
                'stdout': '',
                'stderr': f'Windows OpenSSH 不可用: {ssh_msg}\n\n' +
                         '启用方法（管理员 PowerShell）：\n' +
                         'Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0\n\n' +
                         '或通过设置界面：\n' +
                         '设置 → 应用 → 可选功能 → 添加功能 → OpenSSH 客户端',
                'method': 'native_ssh_windows'
            }

        # 使用原生 SSH 路径（而非 PATH 中优先级更高的 Git SSH）
        native_ssh_exe = ssh_msg  # check_windows_ssh_availability 成功时返回完整路径

        # 转换路径为 Windows 格式
        ssh_config_path_win = ssh_config_path.replace('/', '\\')

        # 构建 PowerShell SSH 命令
        # 使用 & "path" 语法指定原生 SSH，-NoProfile 加快启动
        ssh_cmd = [
            'powershell',
            '-NoProfile',
            '-Command',
            f'& "{native_ssh_exe}" -F "{ssh_config_path_win}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new {alias} "{command}"'
        ]
    else:
        # Unix/Linux：直接执行 SSH
        ssh_cmd = [
            'ssh',
            '-F', ssh_config_path,
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=accept-new',
            alias,
            command
        ]

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace'
        )

        return {
            'success': result.returncode == 0,
            'exit_code': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'method': 'native_ssh_windows' if os.name == 'nt' else 'native_ssh'
        }

    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': f'命令执行超时（{timeout}秒）',
            'method': 'native_ssh_windows' if os.name == 'nt' else 'native_ssh'
        }

    except Exception as e:
        return {
            'success': False,
            'exit_code': -1,
            'stdout': '',
            'stderr': f'执行失败: {str(e)}',
            'method': 'native_ssh_windows' if os.name == 'nt' else 'native_ssh'
        }


def check_ssh_agent() -> Tuple[bool, str]:
    """
    检查 ssh-agent 是否运行且有密钥

    Returns:
        (is_available, message) 元组
    """
    # Windows 特殊处理：直接检查 Windows SSH Agent 服务
    if os.name == 'nt':
        try:
            # 检查服务状态
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ' +
                 'Get-Service ssh-agent | Select-Object Status | ConvertTo-Json'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5
            )

            if result.returncode == 0:
                import json
                service_info = json.loads(result.stdout)
                status = service_info.get('Status', 0)

                if status == 4:  # Running
                    # 使用 Windows 原生 ssh-add（Git 的 ssh-add 无法连接 Windows Agent）
                    native_ssh_add = _get_windows_native_ssh_path('ssh-add.exe')
                    ssh_add_cmd = f'& "{native_ssh_add}" -l' if native_ssh_add else 'ssh-add -l'
                    key_result = subprocess.run(
                        ['powershell', '-NoProfile', '-Command', ssh_add_cmd],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=5
                    )

                    if key_result.returncode == 0:
                        key_count = len([line for line in key_result.stdout.strip().split('\n') if line])
                        return True, f"Windows SSH Agent 运行中，已加载 {key_count} 个密钥"
                    elif key_result.returncode == 1:
                        return False, "Windows SSH Agent 运行中，但未加载任何密钥（运行 ssh-add 添加密钥）"
                else:
                    return False, "Windows SSH Agent 服务未运行"
        except Exception as e:
            pass  # 降级到 Unix 检测逻辑

    # Unix/Linux 检测逻辑
    auth_sock = os.environ.get('SSH_AUTH_SOCK')
    if not auth_sock:
        return False, "ssh-agent 未运行（SSH_AUTH_SOCK 未设置）"

    # 尝试列出密钥
    try:
        result = subprocess.run(
            ['ssh-add', '-l'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            # 有密钥
            key_count = len([line for line in result.stdout.strip().split('\n') if line])
            return True, f"ssh-agent 运行中，已加载 {key_count} 个密钥"
        elif result.returncode == 1:
            # agent 运行但没有密钥
            return False, "ssh-agent 运行中，但未加载任何密钥（运行 ssh-add 添加密钥）"
        else:
            return False, f"ssh-agent 状态异常: {result.stderr}"

    except subprocess.TimeoutExpired:
        return False, "ssh-add 命令超时"
    except FileNotFoundError:
        return False, "ssh-add 命令不存在"
    except Exception as e:
        return False, f"检查 ssh-agent 失败: {str(e)}"
