#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JSON 配置迁移到 SSH Config 工具

功能：
1. 扫描 ~/.ssh/server_config/ 目录下的所有 JSON 配置
2. 转换为标准 SSH config 格式
3. 生成元数据文件
4. 备份原有配置

用法：
    uv run migrate_to_ssh_config.py \\
      --source ~/.ssh/server_config \\
      --output ~/.ssh/config \\
      --metadata ~/.ssh/config_metadata.json \\
      --backup ~/.ssh/server_config.backup
"""

import sys
import os
import json
import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class ConfigMigrator:
    """配置迁移器"""

    def __init__(self, source_dir: str, output_config: str,
                 metadata_file: str, backup_dir: Optional[str] = None,
                 force: bool = False):
        """
        初始化迁移器

        Args:
            source_dir: JSON 配置源目录
            output_config: 输出的 SSH config 文件路径
            metadata_file: 元数据文件路径
            backup_dir: 备份目录路径
            force: 强制执行，跳过确认
        """
        self.source_dir = os.path.expanduser(source_dir)
        self.output_config = os.path.expanduser(output_config)
        self.metadata_file = os.path.expanduser(metadata_file)
        self.backup_dir = os.path.expanduser(backup_dir) if backup_dir else None
        self.force = force

        self.migration_report = {
            "started_at": datetime.now().isoformat(),
            "source_dir": self.source_dir,
            "output_config": self.output_config,
            "metadata_file": self.metadata_file,
            "backup_dir": self.backup_dir,
            "total_files": 0,
            "migrated": 0,
            "skipped": 0,
            "errors": [],
            "warnings": []
        }

    def backup_source(self) -> bool:
        """
        备份源配置目录

        Returns:
            是否成功备份
        """
        if not self.backup_dir:
            return True

        try:
            if os.path.exists(self.backup_dir):
                print(f"警告: 备份目录已存在: {self.backup_dir}")
                return False

            shutil.copytree(self.source_dir, self.backup_dir)
            print(f"[OK] 已备份到: {self.backup_dir}")
            return True

        except Exception as e:
            print(f"[ERROR] 备份失败: {e}")
            return False

    def scan_json_configs(self) -> List[str]:
        """
        扫描 JSON 配置文件

        Returns:
            JSON 文件路径列表
        """
        json_files = []

        if not os.path.exists(self.source_dir):
            print(f"错误: 源目录不存在: {self.source_dir}")
            return json_files

        for filename in os.listdir(self.source_dir):
            if filename.endswith('.json') and not filename.startswith('.'):
                # 跳过 servers.json（这是索引文件）
                if filename == 'servers.json':
                    continue

                file_path = os.path.join(self.source_dir, filename)
                json_files.append(file_path)

        return json_files

    def load_json_config(self, file_path: str) -> Optional[dict]:
        """
        加载 JSON 配置文件

        Args:
            file_path: JSON 文件路径

        Returns:
            配置字典，失败返回 None
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.migration_report['errors'].append({
                'file': file_path,
                'error': str(e)
            })
            return None

    def generate_alias(self, config: dict, filename: str) -> str:
        """
        生成主机别名

        Args:
            config: JSON 配置
            filename: 文件名

        Returns:
            主机别名
        """
        # 优先使用 name 字段
        if 'name' in config:
            return config['name']

        # 否则使用文件名（去掉 .json 后缀）
        return os.path.splitext(os.path.basename(filename))[0]

    def convert_to_ssh_config(self, config: dict, alias: str) -> str:
        """
        转换为 SSH config 格式（带注释元数据）

        Args:
            config: JSON 配置
            alias: 主机别名

        Returns:
            SSH config 文本（包含注释元数据）
        """
        lines = []

        # 生成注释元数据块
        lines.append(f"\n# ===== {alias} =====")

        # 描述
        description = config.get('description', config.get('notes', ''))
        if description:
            lines.append(f"# description: {description}")

        # 环境
        if 'metadata' in config and 'environment' in config['metadata']:
            environment = config['metadata']['environment']
            lines.append(f"# environment: {environment}")

        # 标签
        if 'metadata' in config and 'tags' in config['metadata']:
            tags = config['metadata']['tags']
            if tags:
                lines.append(f"# tags: {','.join(tags)}")

        # 位置
        if 'metadata' in config and 'location' in config['metadata']:
            location = config['metadata']['location']
            lines.append(f"# location: {location}")

        # 创建和更新时间
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if 'metadata' in config and 'created_at' in config['metadata']:
            lines.append(f"# created_at: {config['metadata']['created_at']}")
        else:
            lines.append(f"# created_at: {now}")
        lines.append(f"# updated_at: {now}")

        # Host 配置
        lines.append(f"Host {alias}")

        # 基本配置
        if 'host' in config:
            lines.append(f"    HostName {config['host']}")

        if 'user' in config:
            lines.append(f"    User {config['user']}")

        if 'port' in config and config['port'] != 22:
            lines.append(f"    Port {config['port']}")

        # 密钥文件
        if 'key_file' in config:
            lines.append(f"    IdentityFile {config['key_file']}")

        # 跳板机配置
        if 'jump_hosts' in config and config['jump_hosts']:
            # 处理跳板机列表
            jump_hosts = config['jump_hosts']
            if isinstance(jump_hosts, list) and len(jump_hosts) > 0:
                # 简单处理：只取第一个跳板机
                jump_host = jump_hosts[0]
                if isinstance(jump_host, dict):
                    jump_alias = jump_host.get('name', jump_host.get('host'))
                else:
                    jump_alias = str(jump_host)

                lines.append(f"    ProxyJump {jump_alias}")

                self.migration_report['warnings'].append({
                    'alias': alias,
                    'warning': f'跳板机配置已转换为 ProxyJump: {jump_alias}'
                })

        # 密码认证警告
        if 'password' in config and config['password']:
            self.migration_report['warnings'].append({
                'alias': alias,
                'warning': '使用密码认证，建议转换为密钥认证'
            })

        return '\n'.join(lines)

    def extract_metadata(self, config: dict, alias: str) -> dict:
        """
        提取元数据（用于报告，不再写入单独的文件）

        Args:
            config: JSON 配置
            alias: 主机别名

        Returns:
            元数据字典
        """
        metadata = {}

        # 从 metadata 字段提取
        if 'metadata' in config:
            meta = config['metadata']
            metadata['environment'] = meta.get('environment', 'unknown')
            metadata['tags'] = meta.get('tags', [])
            metadata['location'] = meta.get('location', '')
        else:
            metadata['environment'] = 'unknown'
            metadata['tags'] = []
            metadata['location'] = ''

        # 描述
        if 'description' in config:
            metadata['description'] = config['description']
        elif 'notes' in config:
            metadata['description'] = config['notes']
        else:
            metadata['description'] = ''

        # 迁移时间
        metadata['migrated_at'] = datetime.now().isoformat()
        metadata['original_file'] = alias + '.json'

        return metadata

    def migrate(self) -> dict:
        """
        执行迁移

        Returns:
            迁移报告
        """
        print("=" * 60)
        print("SSH 配置迁移工具")
        print("=" * 60)

        # 备份
        if self.backup_dir:
            print("\n[1/4] 备份原配置...")
            if not self.backup_source():
                if not self.force:
                    print("备份失败，是否继续？(y/n): ", end='')
                    if input().lower() != 'y':
                        return self.migration_report
                else:
                    print("备份失败，但使用 --force 参数，继续执行...")

        # 扫描 JSON 文件
        print("\n[2/4] 扫描 JSON 配置文件...")
        json_files = self.scan_json_configs()
        self.migration_report['total_files'] = len(json_files)
        print(f"找到 {len(json_files)} 个配置文件")

        if len(json_files) == 0:
            print("没有找到需要迁移的配置文件")
            return self.migration_report

        # 转换配置
        print("\n[3/4] 转换配置...")
        ssh_config_lines = []

        for json_file in json_files:
            print(f"  处理: {os.path.basename(json_file)}")

            # 加载 JSON
            config = self.load_json_config(json_file)
            if config is None:
                self.migration_report['skipped'] += 1
                continue

            # 生成别名
            alias = self.generate_alias(config, json_file)

            # 转换为 SSH config（包含注释元数据）
            ssh_config_text = self.convert_to_ssh_config(config, alias)
            ssh_config_lines.append(ssh_config_text)

            # 提取元数据（仅用于报告）
            metadata = self.extract_metadata(config, alias)

            self.migration_report['migrated'] += 1

        # 写入 SSH config
        print("\n[4/4] 写入配置文件...")

        # 备份现有 SSH config（如果存在）
        if os.path.exists(self.output_config):
            backup_config = self.output_config + '.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S')
            shutil.copy2(self.output_config, backup_config)
            print(f"  已备份现有 SSH config 到: {backup_config}")

        # 追加到 SSH config
        with open(self.output_config, 'a', encoding='utf-8') as f:
            f.write('\n# ===== 从 JSON 配置迁移 =====\n')
            f.write(f'# 迁移时间: {datetime.now().isoformat()}\n')
            for config_text in ssh_config_lines:
                f.write(config_text + '\n')

        print(f"  [OK] SSH config 已写入: {self.output_config}")
        print(f"  [INFO] 元数据已嵌入到 SSH config 注释中，无需单独的 metadata 文件")

        # 完成
        self.migration_report['completed_at'] = datetime.now().isoformat()

        print("\n" + "=" * 60)
        print("迁移完成")
        print("=" * 60)
        print(f"总计: {self.migration_report['total_files']} 个文件")
        print(f"成功: {self.migration_report['migrated']} 个")
        print(f"跳过: {self.migration_report['skipped']} 个")
        print(f"错误: {len(self.migration_report['errors'])} 个")
        print(f"警告: {len(self.migration_report['warnings'])} 个")

        if self.migration_report['warnings']:
            print("\n警告信息:")
            for warning in self.migration_report['warnings']:
                print(f"  - {warning['alias']}: {warning['warning']}")

        if self.migration_report['errors']:
            print("\n错误信息:")
            for error in self.migration_report['errors']:
                print(f"  - {error['file']}: {error['error']}")

        return self.migration_report


def main():
    parser = argparse.ArgumentParser(
        description='JSON 配置迁移到 SSH Config 工具',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--source',
        default='~/.ssh/server_config',
        help='JSON 配置源目录（默认: ~/.ssh/server_config）'
    )

    parser.add_argument(
        '--output',
        default='~/.ssh/config',
        help='输出的 SSH config 文件路径（默认: ~/.ssh/config）'
    )

    parser.add_argument(
        '--metadata',
        default='~/.ssh/config_metadata.json',
        help='元数据文件路径（默认: ~/.ssh/config_metadata.json）'
    )

    parser.add_argument(
        '--backup',
        help='备份目录路径（可选）'
    )

    parser.add_argument(
        '--report',
        help='迁移报告输出文件（可选）'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='强制执行，跳过确认（用于非交互环境）'
    )

    args = parser.parse_args()

    # 执行迁移
    migrator = ConfigMigrator(
        source_dir=args.source,
        output_config=args.output,
        metadata_file=args.metadata,
        backup_dir=args.backup,
        force=args.force
    )

    report = migrator.migrate()

    # 保存报告
    if args.report:
        report_path = os.path.expanduser(args.report)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n迁移报告已保存到: {report_path}")


if __name__ == '__main__':
    main()
