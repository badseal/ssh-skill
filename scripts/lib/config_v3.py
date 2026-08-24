"""
配置管理模块 v3.0

基于标准 OpenSSH config 格式的配置加载器

新特性：
1. 从 ~/.ssh/config 加载配置
2. 支持别名（Host）
3. 元数据从注释中解析
4. 完全兼容 ProxyJump（跳板机）
5. 支持密码认证（从注释中读取）
"""

import os
import json
import re
from typing import Dict, Optional, List
from pathlib import Path

try:
    import paramiko
except ImportError:
    raise ImportError("需要安装 paramiko 库: pip install paramiko")


class SSHConfigLoaderV3:
    """SSH Config 加载器 v3.0

    从标准 OpenSSH config 文件加载配置
    """

    def __init__(self, config_path: Optional[str] = None,
                 metadata_path: Optional[str] = None):
        """
        初始化加载器

        Args:
            config_path: SSH config 文件路径，默认 ~/.ssh/config
            metadata_path: 元数据文件路径，默认 ~/.ssh/config_metadata.json
        """
        if config_path is None:
            config_path = os.path.expanduser("~/.ssh/config")
        if metadata_path is None:
            metadata_path = os.path.expanduser("~/.ssh/config_metadata.json")

        self.config_path = config_path
        self.metadata_path = metadata_path

    def load_ssh_config(self, alias: str) -> dict:
        """
        从 SSH config 加载指定别名的配置

        Args:
            alias: 主机别名

        Returns:
            配置字典

        Raises:
            FileNotFoundError: 配置文件不存在
            ValueError: 别名不存在
        """
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"SSH config 文件不存在: {self.config_path}")

        # 解析 SSH config
        ssh_config = paramiko.SSHConfig()
        with open(self.config_path, 'r', encoding='utf-8') as f:
            ssh_config.parse(f)

        # 获取配置
        try:
            host_config = ssh_config.lookup(alias)
        except Exception as e:
            raise ValueError(f"无法解析别名 '{alias}': {e}")

        # 检查是否真的找到了配置（paramiko 会返回默认值）
        if host_config.get('hostname') == alias and not self._alias_exists(alias):
            raise ValueError(f"别名 '{alias}' 不存在于 SSH config 中")

        return host_config

    def _alias_exists(self, alias: str) -> bool:
        """检查别名是否存在于 SSH config 中"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('Host ') and not line.startswith('Host *'):
                        # 提取 Host 名称
                        import re
                        host_match = re.match(r'Host\s+(.+)', line)
                        if host_match and host_match.group(1).strip() == alias:
                            return True
            return False
        except Exception:
            return False

    def list_hosts(self) -> List[str]:
        """返回不含通配符的确定性 Host 别名列表。"""
        if not os.path.exists(self.config_path):
            return []
        aliases = []
        with open(self.config_path, 'r', encoding='utf-8') as stream:
            for line in stream:
                stripped = line.strip()
                if not stripped.startswith('Host ') or stripped.startswith('Host *'):
                    continue
                alias = stripped.split(None, 1)[1].strip()
                if '*' not in alias and '?' not in alias:
                    aliases.append(alias)
        return sorted(set(aliases))

    def load_metadata(self, alias: str) -> dict:
        """
        从注释中加载元数据

        Args:
            alias: 主机别名

        Returns:
            元数据字典（包括密码）
        """
        metadata = {
            'description': '',
            'environment': 'unknown',
            'tags': [],
            'location': '',
            'password': '',
            'warnings': [],
        }

        # 读取 config 文件，查找该 Host 前的注释
        if not os.path.exists(self.config_path):
            return metadata

        with open(self.config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 查找 Host 行
        host_line_index = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('Host ') and not stripped.startswith('Host *'):
                import re
                match = re.match(r'Host\s+(.+)', stripped)
                if match and match.group(1).strip() == alias:
                    host_line_index = i
                    break

        if host_line_index == -1:
            return metadata

        # 向前查找注释
        comment_lines = []
        i = host_line_index - 1
        while i >= 0:
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith('#') or not stripped:
                comment_lines.insert(0, line)
                i -= 1
            else:
                break

        # 解析注释
        for line in comment_lines:
            line = line.strip()
            if not line.startswith('#'):
                continue

            # 移除开头的 #
            line = line[1:].strip()

            # 跳过分隔线
            if line.startswith('=====') or line == '':
                continue

            # 解析 key: value 格式
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()

                if key == 'description':
                    metadata['description'] = value
                elif key == 'environment':
                    metadata['environment'] = value
                elif key == 'tags':
                    metadata['tags'] = [t.strip() for t in value.split(',') if t.strip()]
                elif key == 'location':
                    metadata['location'] = value
                elif key == 'password':
                    metadata['password'] = value

        if metadata.get('password'):
            metadata['warnings'].append('deprecated_plaintext_password')

        return metadata

    def get_connection_params(self, alias: str) -> dict:
        """
        获取连接参数（用于创建 SSH 客户端）

        Args:
            alias: 主机别名

        Returns:
            连接参数字典（包括密码）
        """
        config = self.load_ssh_config(alias)
        metadata = self.load_metadata(alias)

        # 提取连接参数
        params = {
            'hostname': config.get('hostname'),
            'user': config.get('user'),
            'port': int(config.get('port', 22)),
            'timeout': 30,  # 默认超时
            'warnings': list(metadata.get('warnings', [])),
        }

        # 密钥文件
        identity_files = config.get('identityfile')
        if identity_files:
            # paramiko 返回的是列表
            if isinstance(identity_files, list):
                params['key_file'] = identity_files[0]
            else:
                params['key_file'] = identity_files

        # 密码（从注释元数据中获取）
        if metadata.get('password'):
            params['password'] = metadata['password']

        # ProxyJump（跳板机）
        proxy_jump = config.get('proxyjump')
        if proxy_jump:
            params['proxy_jump'] = proxy_jump

        # ForwardAgent（SSH agent 转发）
        forward_agent = config.get('forwardagent', 'no').lower()
        params['forward_agent'] = forward_agent in ('yes', 'true', '1')

        # 元数据
        params['metadata'] = metadata
        params['alias'] = alias

        return params

    def from_alias(self, alias: str):
        """
        通过别名创建 SSH 客户端（智能选择）

        策略：
        - 有密钥文件且无密码 → 使用 NativeSSHClient（原生 SSH）
        - 有密码 → 使用 ParamikoClient（Paramiko）

        Args:
            alias: 主机别名

        Returns:
            NativeSSHClient 或 ParamikoClient 实例
        """
        params = self.get_connection_params(alias)

        has_key = params.get('key_file') is not None
        has_password = params.get('password') is not None

        # 智能选择客户端类型
        if has_key and not has_password:
            # 密钥认证 → 使用原生 SSH（支持 agent forwarding）
            try:
                from .native_ssh_client import NativeSSHClient
            except ImportError:
                from native_ssh_client import NativeSSHClient

            client = NativeSSHClient(
                host=params['hostname'],
                user=params['user'],
                port=params['port'],
                key_file=params.get('key_file'),
                timeout=params['timeout'],
                proxy_jump=params.get('proxy_jump'),
                forward_agent=params.get('forward_agent', False),
                alias=alias
            )
        else:
            # 密码认证 → 使用 Paramiko
            try:
                from .paramiko_client import ParamikoClient
            except ImportError:
                from paramiko_client import ParamikoClient

            client = ParamikoClient(
                host=params['hostname'],
                user=params['user'],
                port=params['port'],
                password=params.get('password'),
                key_file=params.get('key_file'),
                timeout=params['timeout']
            )

        # 设置别名（用于守护进程标识）
        client.alias = alias

        return client

    @staticmethod
    def get_default_config_path() -> str:
        """获取默认 SSH config 路径"""
        return os.path.expanduser("~/.ssh/config")

    @staticmethod
    def get_default_metadata_path() -> str:
        """获取默认元数据路径"""
        return os.path.expanduser("~/.ssh/config_metadata.json")


# 向后兼容：提供全局函数接口
def get_config_loader_v3(config_path: Optional[str] = None,
                         metadata_path: Optional[str] = None) -> SSHConfigLoaderV3:
    """
    获取配置加载器实例

    Args:
        config_path: SSH config 文件路径
        metadata_path: 元数据文件路径

    Returns:
        SSHConfigLoaderV3 实例
    """
    return SSHConfigLoaderV3(config_path, metadata_path)
