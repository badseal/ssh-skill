#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH Config 管理工具 v3.1

基于标准 OpenSSH config 格式的配置管理工具

新特性：
1. 使用标准 ~/.ssh/config 文件
2. 支持别名（Host）管理
3. 元数据存储在注释中（无需单独的 metadata 文件）
4. 支持 ProxyJump（跳板机）配置
5. 保留 config 文件的注释和格式

注释元数据格式：
# ===== 服务器名称 =====
# description: 详细描述
# environment: production|development|staging
# tags: tag1,tag2,tag3
# location: 物理位置
# created_at: 2026-03-01
# updated_at: 2026-03-01
Host alias
    HostName 192.168.1.100
    User root

用法：
    # 列出所有服务器
    python ssh_config_manager_v3.py list-servers

    # 按环境过滤
    python ssh_config_manager_v3.py list-servers --environment production

    # 查找服务器
    python ssh_config_manager_v3.py find "web"

    # 创建配置
    python ssh_config_manager_v3.py create \\
      --alias prod-web-01 \\
      --host 192.168.1.100 \\
      --user root \\
      --key ~/.ssh/id_rsa \\
      --environment production \\
      --description "生产环境 Web 服务器" \\
      --tags web,nginx,production \\
      --location "阿里云华北"

    # 删除配置
    python ssh_config_manager_v3.py delete prod-web-01

    # 导出配置
    python ssh_config_manager_v3.py export --output backup.json
"""

import sys
import os
import json
import argparse
import re
import shutil
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from security import redact_sensitive
from result_protocol import error_result, success_result, write_result

# 修复 Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    import paramiko
except ImportError:
    print("错误: 需要安装 paramiko 库", file=sys.stderr)
    print("请运行: pip install paramiko", file=sys.stderr)
    sys.exit(1)


def backup_file(path: Path | str, timestamp: str | None = None) -> Path:
    source = Path(path)
    stamp = timestamp or datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = source.with_name(f"{source.name}.backup-{stamp}")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(source, backup)
    return backup


def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.writelines(lines)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SSHConfigManager:
    """SSH Config 管理器（基于注释元数据）"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化管理器

        Args:
            config_path: SSH config 文件路径，默认 ~/.ssh/config
        """
        if config_path is None:
            config_path = os.path.expanduser("~/.ssh/config")

        self.config_path = config_path

        # 确保 .ssh 目录存在
        ssh_dir = os.path.dirname(config_path)
        os.makedirs(ssh_dir, exist_ok=True)

        # 确保 config 文件存在
        if not os.path.exists(config_path):
            Path(config_path).touch()

    def parse_metadata_from_comments(self, comment_lines: List[str]) -> dict:
        """
        从注释行中解析元数据

        Args:
            comment_lines: 注释行列表

        Returns:
            元数据字典
        """
        metadata = {}

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

                if key in ['description', 'environment', 'location', 'password', 'created_at', 'updated_at']:
                    metadata[key] = value
                elif key == 'tags':
                    # 标签用逗号分隔
                    metadata['tags'] = [t.strip() for t in value.split(',') if t.strip()]

        return metadata

    def read_config_with_metadata(self) -> List[Tuple[str, dict, List[str], List[str]]]:
        """
        读取配置文件，解析每个 Host 及其元数据

        Returns:
            [(alias, metadata, comment_lines, config_lines)] 列表
        """
        if not os.path.exists(self.config_path):
            return []

        results = []
        current_comments = []
        current_host_comments = []  # 当前 Host 的注释
        current_config = []
        current_alias = None
        in_host_block = False

        with open(self.config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines:
            stripped = line.strip()

            # 检查是否是 Host 行
            if stripped.startswith('Host ') and not stripped.startswith('Host *'):
                # 保存上一个 Host 块
                if current_alias:
                    metadata = self.parse_metadata_from_comments(current_host_comments)
                    results.append((current_alias, metadata, current_host_comments, current_config))

                # 开始新的 Host 块
                host_match = re.match(r'Host\s+(.+)', stripped)
                if host_match:
                    current_alias = host_match.group(1).strip()
                    current_config = [line]
                    current_host_comments = current_comments  # 保存当前收集的注释给这个 Host
                    current_comments = []  # 清空，准备收集下一个 Host 的注释
                    in_host_block = True

            elif in_host_block:
                # 在 Host 块中
                if stripped and not stripped.startswith('#'):
                    # 配置行（缩进的）
                    if line.startswith((' ', '\t')):
                        current_config.append(line)
                    else:
                        # 遇到非缩进的非注释行，Host 块结束
                        in_host_block = False
                        if stripped.startswith('#'):
                            current_comments.append(line)
                elif stripped.startswith('#'):
                    # Host 块中的注释（通常不应该有）
                    current_config.append(line)
                elif not stripped:
                    # 空行，Host 块结束
                    current_config.append(line)
                    in_host_block = False

            else:
                # 不在 Host 块中，收集注释
                if stripped.startswith('#') or not stripped:
                    current_comments.append(line)
                else:
                    # 非注释非空行，清空注释缓存
                    current_comments = []

        # 保存最后一个 Host 块
        if current_alias:
            metadata = self.parse_metadata_from_comments(current_host_comments)
            results.append((current_alias, metadata, current_host_comments, current_config))

        return results

    def parse_config(self) -> paramiko.SSHConfig:
        """解析 SSH config 文件"""
        ssh_config = paramiko.SSHConfig()
        with open(self.config_path, 'r', encoding='utf-8') as f:
            ssh_config.parse(f)
        return ssh_config

    def get_host_config(self, alias: str) -> Optional[dict]:
        """
        获取指定别名的配置

        Args:
            alias: 主机别名

        Returns:
            配置字典，未找到返回 None
        """
        try:
            # 先检查别名是否存在
            hosts = self.list_hosts()
            if alias not in hosts:
                return None

            ssh_config = self.parse_config()
            host_config = ssh_config.lookup(alias)

            return host_config
        except Exception:
            return None

    def list_hosts(self) -> List[str]:
        """
        列出所有 Host 别名

        Returns:
            Host 别名列表
        """
        hosts = []
        with open(self.config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('Host ') and not line.startswith('Host *'):
                    host_match = re.match(r'Host\s+(.+)', line)
                    if host_match:
                        host_name = host_match.group(1).strip()
                        hosts.append(host_name)
        return hosts

    def create_host(self, alias: str, hostname: str, user: str,
                   identity_file: Optional[str] = None, port: int = 22,
                   proxy_jump: Optional[str] = None,
                   environment: str = "development",
                   description: str = "",
                   tags: Optional[List[str]] = None,
                   location: str = "") -> bool:
        """
        创建新的 Host 配置（带注释元数据）

        Args:
            alias: 主机别名
            hostname: 主机地址
            user: 用户名
            identity_file: 密钥文件路径
            port: 端口号
            proxy_jump: 跳板机别名
            environment: 环境类型
            description: 描述
            tags: 标签列表
            location: 物理位置

        Returns:
            是否成功创建
        """
        # 检查别名是否已存在
        if self.get_host_config(alias) is not None:
            raise ValueError(f"别名 '{alias}' 已存在")

        # 构建注释元数据块
        comment_lines = [
            f"\n# ===== {alias} =====\n"
        ]

        if description:
            comment_lines.append(f"# description: {description}\n")

        if environment:
            comment_lines.append(f"# environment: {environment}\n")

        if tags:
            comment_lines.append(f"# tags: {','.join(tags)}\n")

        if location:
            comment_lines.append(f"# location: {location}\n")

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        comment_lines.append(f"# created_at: {now}\n")
        comment_lines.append(f"# updated_at: {now}\n")

        # 构建配置块
        config_lines = [
            f"Host {alias}\n",
            f"    HostName {hostname}\n",
            f"    User {user}\n",
        ]

        if port != 22:
            config_lines.append(f"    Port {port}\n")

        if identity_file:
            config_lines.append(f"    IdentityFile {identity_file}\n")

        if proxy_jump:
            config_lines.append(f"    ProxyJump {proxy_jump}\n")

        # 添加到 config 文件
        with open(self.config_path, 'a', encoding='utf-8') as f:
            f.writelines(comment_lines)
            f.writelines(config_lines)

        return True

    def update_host(self, alias: str,
                   hostname: Optional[str] = None,
                   user: Optional[str] = None,
                   port: Optional[int] = None,
                   identity_file: Optional[str] = None,
                   proxy_jump: Optional[str] = None,
                   environment: Optional[str] = None,
                   description: Optional[str] = None,
                   tags: Optional[List[str]] = None,
                   location: Optional[str] = None) -> bool:
        """
        更新 Host 配置（包括注释元数据）

        Args:
            alias: 主机别名
            hostname: 主机地址（可选）
            user: 用户名（可选）
            port: 端口号（可选）
            identity_file: 密钥文件路径（可选）
            proxy_jump: 跳板机别名（可选）
            environment: 环境类型（可选）
            description: 描述（可选）
            tags: 标签列表（可选）
            location: 物理位置（可选）

        Returns:
            是否成功更新
        """
        # 检查别名是否存在
        if self.get_host_config(alias) is None:
            raise ValueError(f"别名 '{alias}' 不存在")

        # 读取配置文件
        with open(self.config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 查找并更新配置块
        new_lines = []
        i = 0
        skip_comments = []
        found = False

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 收集注释行
            if stripped.startswith('#') or not stripped:
                skip_comments.append(line)
                i += 1
                continue

            # 检查是否是目标 Host 行
            if stripped.startswith('Host '):
                host_match = re.match(r'Host\s+(.+)', stripped)
                if host_match and host_match.group(1).strip() == alias:
                    found = True
                    # 找到目标 Host，更新元数据注释
                    updated_comments = self._update_metadata_comments(
                        skip_comments, alias, environment, description, tags, location
                    )
                    new_lines.extend(updated_comments)
                    skip_comments = []

                    # 添加 Host 行
                    new_lines.append(line)
                    i += 1

                    # 更新配置行
                    config_lines = []
                    while i < len(lines):
                        next_line = lines[i]
                        next_stripped = next_line.strip()

                        # 如果遇到非缩进的非注释行，配置块结束
                        if next_stripped and not next_line.startswith((' ', '\t', '#')):
                            break

                        # 收集当前配置行
                        if next_line.startswith((' ', '\t')) and next_stripped:
                            config_lines.append(next_line)
                        elif not next_stripped:
                            # 空行，配置块结束
                            break

                        i += 1

                    # 更新配置行
                    updated_config = self._update_config_lines(
                        config_lines, hostname, user, port, identity_file, proxy_jump
                    )
                    new_lines.extend(updated_config)

                    continue
                else:
                    # 不是目标 Host，保留收集的注释和这个 Host
                    new_lines.extend(skip_comments)
                    skip_comments = []
                    new_lines.append(line)
                    i += 1
            else:
                # 其他行，保留收集的注释和这一行
                new_lines.extend(skip_comments)
                skip_comments = []
                new_lines.append(line)
                i += 1

        # 保留最后的注释（如果有）
        new_lines.extend(skip_comments)

        if not found:
            return False

        # 写回文件
        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        return True

    def _update_metadata_comments(self, comment_lines: List[str], alias: str,
                                  environment: Optional[str],
                                  description: Optional[str],
                                  tags: Optional[List[str]],
                                  location: Optional[str]) -> List[str]:
        """更新元数据注释"""
        # 解析现有元数据
        metadata = self.parse_metadata_from_comments(comment_lines)

        # 更新元数据（只更新提供的字段）
        if environment is not None:
            metadata['environment'] = environment
        if description is not None:
            metadata['description'] = description
        if tags is not None:
            metadata['tags'] = tags
        if location is not None:
            metadata['location'] = location

        # 更新时间戳
        metadata['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 保留 created_at（如果存在）
        if 'created_at' not in metadata:
            metadata['created_at'] = metadata['updated_at']

        # 重新构建注释块
        new_comments = [f"\n# ===== {alias} =====\n"]

        if metadata.get('description'):
            new_comments.append(f"# description: {metadata['description']}\n")

        if metadata.get('environment'):
            new_comments.append(f"# environment: {metadata['environment']}\n")

        if metadata.get('tags'):
            tags_str = ','.join(metadata['tags']) if isinstance(metadata['tags'], list) else metadata['tags']
            new_comments.append(f"# tags: {tags_str}\n")

        if metadata.get('location'):
            new_comments.append(f"# location: {metadata['location']}\n")

        # 保留 password 字段（如果存在）
        if metadata.get('password'):
            new_comments.append(f"# password: {metadata['password']}\n")

        new_comments.append(f"# created_at: {metadata['created_at']}\n")
        new_comments.append(f"# updated_at: {metadata['updated_at']}\n")

        return new_comments

    def _update_config_lines(self, config_lines: List[str],
                            hostname: Optional[str],
                            user: Optional[str],
                            port: Optional[int],
                            identity_file: Optional[str],
                            proxy_jump: Optional[str]) -> List[str]:
        """更新配置行"""
        # 解析现有配置
        config_dict = {}
        for line in config_lines:
            stripped = line.strip()
            if not stripped:
                continue

            parts = stripped.split(None, 1)
            if len(parts) == 2:
                key, value = parts
                config_dict[key.lower()] = value

        # 更新配置（只更新提供的字段）
        if hostname is not None:
            config_dict['hostname'] = hostname
        if user is not None:
            config_dict['user'] = user
        if port is not None:
            config_dict['port'] = str(port)
        if identity_file is not None:
            config_dict['identityfile'] = identity_file
        if proxy_jump is not None:
            config_dict['proxyjump'] = proxy_jump

        # 重新构建配置行（保持标准顺序）
        new_config = []

        # 标准字段顺序
        field_order = ['hostname', 'user', 'port', 'identityfile', 'proxyjump', 'forwardagent']

        for field in field_order:
            if field in config_dict:
                # 首字母大写
                field_name = field.capitalize()
                if field == 'hostname':
                    field_name = 'HostName'
                elif field == 'identityfile':
                    field_name = 'IdentityFile'
                elif field == 'proxyjump':
                    field_name = 'ProxyJump'
                elif field == 'forwardagent':
                    field_name = 'ForwardAgent'

                new_config.append(f"    {field_name} {config_dict[field]}\n")

        # 添加其他未知字段
        for key, value in config_dict.items():
            if key not in field_order:
                field_name = key.capitalize()
                new_config.append(f"    {field_name} {value}\n")

        return new_config

    def preview_delete_host(self, alias: str) -> Optional[dict]:
        """返回删除范围和脱敏摘要，不修改配置。"""
        path = Path(self.config_path)
        lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
        host_index = None
        for index, line in enumerate(lines):
            match = re.match(r'Host\s+(.+)', line.strip())
            if match and match.group(1).strip() == alias:
                host_index = index
                break
        if host_index is None:
            return None

        start = host_index
        while start > 0:
            previous = lines[start - 1].strip()
            if previous and not previous.startswith('#'):
                break
            start -= 1

        end = host_index + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith('Host ') or stripped.startswith('# ====='):
                break
            end += 1

        config = self.get_host_config(alias) or {}
        metadata = self.parse_metadata_from_comments(lines[start:host_index])
        identity_files = config.get('identityfile', [])
        identity_file = (
            identity_files[0]
            if isinstance(identity_files, list) and identity_files
            else identity_files or None
        )
        return {
            'alias': alias,
            'hostname': config.get('hostname'),
            'user': config.get('user'),
            'port': int(config.get('port', 22)),
            'identity_file': identity_file,
            'environment': metadata.get('environment', 'unknown'),
            'description': metadata.get('description', ''),
            'line_start': start + 1,
            'line_end': end,
            '_start': start,
            '_end': end,
            '_lines': lines,
        }

    def delete_host(
        self,
        alias: str,
        *,
        apply: bool = False,
        timestamp: str | None = None,
    ) -> dict:
        """预览或在备份后原子删除 Host 配置。"""
        preview = self.preview_delete_host(alias)
        if preview is None:
            return {'applied': False, 'found': False, 'alias': alias}

        public_preview = {
            key: value for key, value in preview.items() if not key.startswith('_')
        }
        if not apply:
            return {'applied': False, 'found': True, 'host': public_preview}

        path = Path(self.config_path)
        backup = backup_file(path, timestamp)
        new_lines = (
            preview['_lines'][:preview['_start']]
            + preview['_lines'][preview['_end']:]
        )
        _atomic_write_lines(path, new_lines)
        return {
            'applied': True,
            'found': True,
            'host': public_preview,
            'backup_path': str(backup),
        }

    def find_host(self, query: str) -> List[Tuple[str, dict, dict]]:
        """
        查找主机（支持别名、描述、标签、位置模糊匹配）

        Args:
            query: 查询字符串

        Returns:
            [(alias, config, metadata)] 列表
        """
        results = []
        query_lower = query.lower()

        hosts_with_metadata = self.read_config_with_metadata()

        for alias, metadata, _, _ in hosts_with_metadata:
            # 精确匹配别名
            if alias.lower() == query_lower:
                config = self.get_host_config(alias)
                results.append((alias, config, metadata))
                continue

            # 模糊匹配别名
            if query_lower in alias.lower():
                config = self.get_host_config(alias)
                results.append((alias, config, metadata))
                continue

            # 模糊匹配描述
            if query_lower in metadata.get('description', '').lower():
                config = self.get_host_config(alias)
                results.append((alias, config, metadata))
                continue

            # 模糊匹配位置
            if query_lower in metadata.get('location', '').lower():
                config = self.get_host_config(alias)
                results.append((alias, config, metadata))
                continue

            # 模糊匹配标签
            if any(query_lower in tag.lower() for tag in metadata.get('tags', [])):
                config = self.get_host_config(alias)
                results.append((alias, config, metadata))

        return results

    def list_servers(self, environment: Optional[str] = None,
                    tags: Optional[List[str]] = None) -> List[Tuple[str, dict, dict]]:
        """
        列出所有服务器

        Args:
            environment: 按环境过滤
            tags: 按标签过滤

        Returns:
            [(alias, config, metadata)] 列表
        """
        results = []
        hosts_with_metadata = self.read_config_with_metadata()

        for alias, metadata, _, _ in hosts_with_metadata:
            # 环境过滤
            if environment and metadata.get('environment') != environment:
                continue

            # 标签过滤
            if tags:
                host_tags = metadata.get('tags', [])
                if not any(tag in host_tags for tag in tags):
                    continue

            config = self.get_host_config(alias)
            results.append((alias, config, metadata))

        return results

    def export_config(self) -> dict:
        """
        导出所有配置（用于备份）

        Returns:
            配置字典
        """
        export_data = {
            "version": "3.1",
            "exported_at": datetime.now().isoformat(),
            "hosts": [],
            "warnings": [],
        }

        hosts_with_metadata = self.read_config_with_metadata()

        for alias, metadata, _, _ in hosts_with_metadata:
            config = self.get_host_config(alias)
            if metadata.get('password'):
                export_data['warnings'].append('deprecated_plaintext_password')

            host_data = {
                "alias": alias,
                "hostname": config.get('hostname'),
                "user": config.get('user'),
                "port": config.get('port', 22),
                "identity_file": config.get('identityfile', [None])[0] if config.get('identityfile') else None,
                "proxy_jump": config.get('proxyjump'),
                "metadata": redact_sensitive(metadata)
            }

            export_data['hosts'].append(host_data)

        export_data['warnings'] = sorted(set(export_data['warnings']))
        return export_data


def _get_auth_method(config, meta) -> str:
    """判断认证方式"""
    has_password = bool(meta.get('password'))
    identity_files = config.get('identityfile', [])
    has_key = bool(identity_files and identity_files[0])

    if has_password and has_key:
        return "密码+密钥"
    elif has_password:
        return "密码"
    elif has_key:
        return "密钥"
    else:
        return "未配置"


def _load_password_metadata(manager, alias) -> str:
    """从注释中加载密码信息（仅判断有无）"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        meta = loader.load_metadata(alias)
        return meta.get('password', '')
    except Exception:
        return ''


def cmd_list_servers(args):
    """列出所有服务器"""
    try:
        manager = SSHConfigManager()
        servers = manager.list_servers(
            environment=args.environment,
            tags=args.tags
        )

        if not servers:
            print(json.dumps({
                'success': True,
                'message': '未找到服务器',
                'servers': []
            }, ensure_ascii=False, indent=2))
            return

        result_list = []
        warnings = []
        for alias, config, meta in servers:
            # 补充密码信息（从 config_v3 加载，注释元数据中的 password 字段）
            if 'password' not in meta:
                meta['password'] = _load_password_metadata(manager, alias)
            if meta.get('password'):
                warnings.append('deprecated_plaintext_password')

            result_list.append({
                'alias': alias,
                'hostname': config.get('hostname'),
                'user': config.get('user'),
                'port': config.get('port', 22),
                'description': meta.get('description', ''),
                'tags': meta.get('tags', []),
                'location': meta.get('location', ''),
                'auth': _get_auth_method(config, meta),
            })

        print(json.dumps({
            'success': True,
            'count': len(result_list),
            'servers': result_list,
            'warnings': sorted(set(warnings)),
        }, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def cmd_find(args):
    """查找服务器"""
    try:
        manager = SSHConfigManager()
        results = manager.find_host(args.query)

        if not results:
            print(json.dumps({
                'success': True,
                'message': f'未找到匹配 "{args.query}" 的服务器',
                'results': []
            }, ensure_ascii=False, indent=2))
            return

        result_list = []
        for alias, config, meta in results:
            result_list.append({
                'alias': alias,
                'hostname': config.get('hostname'),
                'user': config.get('user'),
                'port': config.get('port', 22),
                'environment': meta.get('environment', 'unknown'),
                'description': meta.get('description', ''),
                'tags': meta.get('tags', []),
                'location': meta.get('location', '')
            })

        print(json.dumps({
            'success': True,
            'count': len(result_list),
            'results': result_list
        }, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def cmd_create(args):
    """创建服务器配置"""
    if getattr(args, 'password', None) is not None:
        print(json.dumps({
            'success': False,
            'error': {
                'code': 'plaintext_password_write_disabled',
                'message': '不再允许将新密码写入 SSH config，请改用密钥或系统凭据存储',
            },
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

    try:
        manager = SSHConfigManager()

        success = manager.create_host(
            alias=args.alias,
            hostname=args.host,
            user=args.user,
            identity_file=args.key,
            port=args.port,
            proxy_jump=args.jump,
            environment=args.environment,
            description=args.description or "",
            tags=args.tags or [],
            location=args.location or ""
        )

        if success:
            print(json.dumps({
                'success': True,
                'message': f'服务器 {args.alias} 创建成功',
                'alias': args.alias,
                'config_file': manager.config_path
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                'success': False,
                'error': '创建失败'
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def cmd_update(args):
    """更新服务器配置"""
    if getattr(args, 'password', None) is not None:
        print(json.dumps({
            'success': False,
            'error': {
                'code': 'plaintext_password_write_disabled',
                'message': '不再允许将新密码写入 SSH config，请改用密钥或系统凭据存储',
            },
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

    try:
        manager = SSHConfigManager()

        # 准备更新参数（只传递非 None 的参数）
        update_kwargs = {'alias': args.alias}

        if args.host is not None:
            update_kwargs['hostname'] = args.host
        if args.user is not None:
            update_kwargs['user'] = args.user
        if args.port is not None:
            update_kwargs['port'] = args.port
        if args.key is not None:
            update_kwargs['identity_file'] = args.key
        if args.jump is not None:
            update_kwargs['proxy_jump'] = args.jump
        if args.environment is not None:
            update_kwargs['environment'] = args.environment
        if args.description is not None:
            update_kwargs['description'] = args.description
        if args.tags is not None:
            update_kwargs['tags'] = args.tags
        if args.location is not None:
            update_kwargs['location'] = args.location

        success = manager.update_host(**update_kwargs)

        if success:
            print(json.dumps({
                'success': True,
                'message': f'服务器 {args.alias} 更新成功',
                'alias': args.alias,
                'config_file': manager.config_path
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                'success': False,
                'error': '更新失败'
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def cmd_delete(args):
    """删除服务器配置"""
    try:
        manager = SSHConfigManager()
        mutation = manager.delete_host(
            args.alias,
            apply=getattr(args, 'apply', False),
        )
        if not mutation.get('found'):
            result = error_result(
                'config.delete',
                code='host_not_found',
                message=f'服务器 {args.alias} 不存在',
            )
            write_result(result, stream=sys.stderr)
            raise SystemExit(1)

        data = {
            'mode': 'apply' if mutation['applied'] else 'preview',
            'host': mutation['host'],
        }
        if mutation.get('backup_path'):
            data['backup_path'] = mutation['backup_path']
        write_result(success_result('config.delete', data), stream=sys.stdout)

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def cmd_export(args):
    """导出配置"""
    try:
        manager = SSHConfigManager()
        export_data = manager.export_config()

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            print(json.dumps({
                'success': True,
                'message': f'配置已导出到 {args.output}',
                'count': len(export_data['hosts'])
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(export_data, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def _legacy_main(argv=None):
    parser = argparse.ArgumentParser(
        description='SSH Config 管理工具 v3.1（基于注释元数据）',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # list-servers 命令
    list_parser = subparsers.add_parser('list-servers', help='列出所有服务器')
    list_parser.add_argument('--environment', help='按环境过滤')
    list_parser.add_argument('--tags', nargs='+', help='按标签过滤')

    # find 命令
    find_parser = subparsers.add_parser('find', help='查找服务器')
    find_parser.add_argument('query', help='查询字符串')

    # create 命令
    create_parser = subparsers.add_parser('create', help='创建服务器配置')
    create_parser.add_argument('--alias', required=True, help='主机别名')
    create_parser.add_argument('--host', required=True, help='主机地址')
    create_parser.add_argument('--user', required=True, help='用户名')
    create_parser.add_argument('--key', help='密钥文件路径')
    create_parser.add_argument('--password', help='已禁用：不得写入明文密码')
    create_parser.add_argument('--port', type=int, default=22, help='端口号')
    create_parser.add_argument('--jump', help='跳板机别名')
    create_parser.add_argument('--environment', default='development', help='环境类型')
    create_parser.add_argument('--description', help='描述')
    create_parser.add_argument('--tags', nargs='+', help='标签列表')
    create_parser.add_argument('--location', help='物理位置')

    # update 命令
    update_parser = subparsers.add_parser('update', help='更新服务器配置')
    update_parser.add_argument('alias', help='主机别名')
    update_parser.add_argument('--host', help='主机地址')
    update_parser.add_argument('--user', help='用户名')
    update_parser.add_argument('--key', help='密钥文件路径')
    update_parser.add_argument('--password', help='已禁用：不得写入明文密码')
    update_parser.add_argument('--port', type=int, help='端口号')
    update_parser.add_argument('--jump', help='跳板机别名')
    update_parser.add_argument('--environment', help='环境类型')
    update_parser.add_argument('--description', help='描述')
    update_parser.add_argument('--tags', nargs='+', help='标签列表')
    update_parser.add_argument('--location', help='物理位置')

    # delete 命令
    delete_parser = subparsers.add_parser('delete', help='删除服务器配置')
    delete_parser.add_argument('alias', help='主机别名')
    delete_parser.add_argument('--apply', action='store_true',
                               help='确认备份并删除配置')

    # export 命令
    export_parser = subparsers.add_parser('export', help='导出配置')
    export_parser.add_argument('--output', help='输出文件路径')

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 执行命令
    if args.command == 'list-servers':
        cmd_list_servers(args)
    elif args.command == 'find':
        cmd_find(args)
    elif args.command == 'create':
        cmd_create(args)
    elif args.command == 'update':
        cmd_update(args)
    elif args.command == 'delete':
        cmd_delete(args)
    elif args.command == 'export':
        cmd_export(args)


def main(argv=None):
    from ssh_skill import delegate_legacy_entrypoint

    arguments = sys.argv[1:] if argv is None else list(argv)
    return delegate_legacy_entrypoint(
        'config', arguments, legacy_main=_legacy_main
    )


if __name__ == '__main__':
    raise SystemExit(main())
