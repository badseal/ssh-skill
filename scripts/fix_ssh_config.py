#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 SSH Config 文件

1. 从原始 JSON 配置中提取元数据填充注释
2. 统一证书文件路径格式为 ~/.ssh/keyfile
3. 在注释中添加密码字段（如果有）
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path


def load_json_config(json_path):
    """加载 JSON 配置文件"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  警告: 无法加载 {json_path}: {e}")
        return None


def find_json_config_by_alias(alias, json_dir):
    """根据别名查找对应的 JSON 配置文件"""
    # 尝试直接匹配
    json_path = os.path.join(json_dir, f"{alias}.json")
    if os.path.exists(json_path):
        return load_json_config(json_path)

    # 尝试大小写不敏感匹配
    for filename in os.listdir(json_dir):
        if not filename.endswith('.json'):
            continue

        name_without_ext = os.path.splitext(filename)[0]
        if name_without_ext.upper() == alias.upper():
            json_path = os.path.join(json_dir, filename)
            return load_json_config(json_path)

    # 尝试从 JSON 中的 name 字段匹配
    for filename in os.listdir(json_dir):
        if not filename.endswith('.json'):
            continue

        json_path = os.path.join(json_dir, filename)
        config = load_json_config(json_path)
        if config and config.get('name') == alias:
            return config

    return None


def normalize_key_path(key_path):
    """
    统一证书文件路径格式为 ~/.ssh/keyfile

    Args:
        key_path: 原始路径

    Returns:
        标准化后的路径
    """
    if not key_path:
        return key_path

    # 已经是 ~/.ssh/ 格式，直接返回
    if key_path.startswith('~/.ssh/'):
        return key_path

    # Windows 绝对路径转换为 ~/.ssh/ 格式
    # C:\Users\zhangyang\.ssh\keyfile -> ~/.ssh/keyfile
    # C:\Users\zhangyang/.ssh\keyfile -> ~/.ssh/keyfile
    # C:/Users/zhangyang/.ssh/keyfile -> ~/.ssh/keyfile

    # 统一路径分隔符
    normalized = key_path.replace('\\', '/')

    # 提取 .ssh 之后的部分
    if '/.ssh/' in normalized:
        parts = normalized.split('/.ssh/')
        if len(parts) == 2:
            return f"~/.ssh/{parts[1]}"

    # 如果无法转换，返回原路径
    return key_path


def extract_metadata_from_json(config):
    """从 JSON 配置中提取元数据"""
    metadata = {
        'description': '',
        'environment': 'unknown',
        'tags': [],
        'location': '',
        'password': ''
    }

    # 描述
    if 'description' in config:
        metadata['description'] = config['description']
    elif 'notes' in config:
        metadata['description'] = config['notes']

    # 从 metadata 字段提取
    if 'metadata' in config:
        meta = config['metadata']
        metadata['environment'] = meta.get('environment', 'unknown')
        metadata['tags'] = meta.get('tags', [])
        metadata['location'] = meta.get('location', '')

    # 密码
    if 'password' in config and config['password']:
        metadata['password'] = config['password']

    return metadata


def parse_ssh_config(config_path):
    """
    解析 SSH config 文件

    Returns:
        List of blocks, each block is a dict with:
        - comments: list of comment lines
        - host_line: the Host line
        - config_lines: list of config lines
        - alias: extracted alias
    """
    if not os.path.exists(config_path):
        return []

    with open(config_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    blocks = []
    current_comments = []
    current_host_line = None
    current_config = []
    in_host_block = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 检查是否是 Host 行
        if stripped.startswith('Host ') and not stripped.startswith('Host *'):
            # 保存上一个 Host 块
            if current_host_line:
                alias = extract_alias_from_host_line(current_host_line)
                blocks.append({
                    'comments': current_comments,
                    'host_line': current_host_line,
                    'config_lines': current_config,
                    'alias': alias
                })

            # 开始新的 Host 块
            current_host_line = line
            current_config = []
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
                    current_comments = []
                    if stripped.startswith('#'):
                        current_comments.append(line)
            elif stripped.startswith('#'):
                current_config.append(line)
            elif not stripped:
                current_config.append(line)
                in_host_block = False
                current_comments = []
        else:
            # 不在 Host 块中
            if stripped.startswith('#') or not stripped:
                current_comments.append(line)
            else:
                current_comments = []

        i += 1

    # 保存最后一个 Host 块
    if current_host_line:
        alias = extract_alias_from_host_line(current_host_line)
        blocks.append({
            'comments': current_comments,
            'host_line': current_host_line,
            'config_lines': current_config,
            'alias': alias
        })

    return blocks


def extract_alias_from_host_line(host_line):
    """从 Host 行提取别名"""
    match = re.match(r'Host\s+(.+)', host_line.strip())
    if match:
        return match.group(1).strip()
    return None


def generate_updated_comments(alias, metadata):
    """生成更新后的注释"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    comments = [
        f"\n# ===== {alias} =====\n",
        f"# description: {metadata.get('description', '')}\n",
        f"# environment: {metadata.get('environment', 'unknown')}\n",
    ]

    # 标签
    tags = metadata.get('tags', [])
    if tags:
        comments.append(f"# tags: {','.join(tags)}\n")
    else:
        comments.append(f"# tags: \n")

    # 位置
    location = metadata.get('location', '')
    comments.append(f"# location: {location}\n")

    # 时间
    comments.append(f"# created_at: {now}\n")
    comments.append(f"# updated_at: {now}\n")

    return comments


def normalize_config_lines(config_lines):
    """标准化配置行中的证书路径"""
    normalized = []

    for line in config_lines:
        # 检查是否是 IdentityFile 行
        if 'IdentityFile' in line:
            match = re.match(r'(\s*)IdentityFile\s+(.+)', line)
            if match:
                indent = match.group(1)
                key_path = match.group(2).strip()
                normalized_path = normalize_key_path(key_path)
                normalized.append(f"{indent}IdentityFile {normalized_path}\n")
                continue

        normalized.append(line)

    return normalized


def fix_ssh_config(config_path, json_dir, output_path=None):
    """
    修复 SSH config 文件

    Args:
        config_path: SSH config 文件路径
        json_dir: JSON 配置目录
        output_path: 输出文件路径（如果为 None，则覆盖原文件）
    """
    if output_path is None:
        output_path = config_path

    # 解析现有配置
    blocks = parse_ssh_config(config_path)

    print(f"找到 {len(blocks)} 个 Host 配置")

    # 处理每个块
    new_lines = []
    updated_count = 0
    normalized_count = 0
    password_count = 0

    for block in blocks:
        alias = block['alias']

        if not alias:
            # 无法提取别名，保持原样
            new_lines.extend(block['comments'])
            new_lines.append(block['host_line'])
            new_lines.extend(block['config_lines'])
            continue

        # 查找对应的 JSON 配置
        json_config = find_json_config_by_alias(alias, json_dir)

        if json_config:
            # 提取元数据
            metadata = extract_metadata_from_json(json_config)

            # 生成新注释
            new_comments = generate_updated_comments(alias, metadata)
            new_lines.extend(new_comments)

            updated_count += 1

            if metadata.get('password'):
                password_count += 1
                print(f"  更新 {alias}（已跳过旧明文密码）")
            else:
                print(f"  更新 {alias}")
        else:
            # 没有找到 JSON 配置，保持原注释
            new_lines.extend(block['comments'])
            print(f"  跳过 {alias}（未找到 JSON 配置）")

        # Host 行
        new_lines.append(block['host_line'])

        # 标准化配置行中的证书路径
        normalized_config = normalize_config_lines(block['config_lines'])

        # 检查是否有路径被标准化
        if normalized_config != block['config_lines']:
            normalized_count += 1

        new_lines.extend(normalized_config)

    # 写入新配置
    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    print(f"\n完成:")
    print(f"  更新元数据: {updated_count} 个")
    print(f"  标准化路径: {normalized_count} 个")
    print(f"  跳过旧明文密码: {password_count} 个")
    print(f"  输出: {output_path}")


if __name__ == '__main__':
    config_path = os.path.expanduser('~/.ssh/config')
    json_dir = os.path.expanduser('~/.ssh/server_config')

    fix_ssh_config(config_path, json_dir)
