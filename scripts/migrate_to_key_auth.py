#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迁移服务器从密码认证到密钥认证

更新 SSH config：
1. 移除 password 字段
2. 添加 IdentityFile 配置
3. 不复制、打印或另存原密码
"""

import sys
import os
import re

# 修复 Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def migrate_to_key_auth(alias, key_file):
    """
    迁移服务器配置从密码认证到密钥认证

    Args:
        alias: 服务器别名
        key_file: 密钥文件名（如 id_rsa_sa_legacy）

    Returns:
        bool: 是否成功
    """
    config_path = os.path.expanduser("~/.ssh/config")

    if not os.path.exists(config_path):
        print(f"错误: SSH config 文件不存在: {config_path}")
        return False

    with open(config_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 查找该 Host 的位置
    host_index = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('Host ') and not line.strip().startswith('Host *'):
            match = re.match(r'Host\s+(.+)', line.strip())
            if match and match.group(1).strip() == alias:
                host_index = i
                break

    if host_index == -1:
        print(f"错误: 找不到服务器 {alias}")
        return False

    # 向前查找注释块
    comment_start = host_index
    for i in range(host_index - 1, max(0, host_index - 20), -1):
        line = lines[i].strip()
        if line.startswith('# ====='):
            comment_start = i
            break
        if not line.startswith('#') and line:
            break

    # 查找 password 和 tags 字段
    password_index = -1
    password_value = None
    tags_index = -1
    tags_value = []

    for i in range(comment_start, host_index):
        line = lines[i].strip()
        if line.startswith('# password:'):
            password_index = i
            password_value = line[11:].strip()
        elif line.startswith('# tags:'):
            tags_index = i
            tags_value = [t.strip() for t in line[7:].strip().split(',') if t.strip()]

    if not password_value:
        print(f"警告: {alias} 没有配置密码，可能已经是密钥认证")
        return False

    # 移除 password 行
    if password_index != -1:
        lines[password_index] = ''

    # 更新 tags 行
    if tags_index != -1:
        lines[tags_index] = f"# tags: {','.join(tags_value)}\n"
    else:
        # 在 Host 行前添加 tags
        lines.insert(host_index, f"# tags: {','.join(tags_value)}\n")
        host_index += 1

    # 查找 Host 块的结束位置
    host_end = host_index + 1
    for i in range(host_index + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith('Host ') and not line.startswith('Host *'):
            break
        if line.startswith('# ====='):
            break
        host_end = i + 1

    # 检查是否已有 IdentityFile
    has_identity_file = False
    for i in range(host_index, host_end):
        if 'IdentityFile' in lines[i]:
            has_identity_file = True
            break

    # 添加 IdentityFile（如果不存在）
    if not has_identity_file:
        # 在 Host 块的最后添加
        indent = '    '
        lines.insert(host_end, f"{indent}IdentityFile ~/.ssh/{key_file}\n")

    # 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    print(f"✓ 已将 {alias} 迁移到密钥认证")
    print(f"  - 密钥文件: ~/.ssh/{key_file}")
    print(f"  - 原明文密码已从配置中移除")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description='迁移服务器从密码认证到密钥认证')
    parser.add_argument('alias', help='服务器别名')
    parser.add_argument('--key-file', required=True, help='密钥文件名（如 id_rsa_sa_legacy）')

    args = parser.parse_args()

    success = migrate_to_key_auth(args.alias, args.key_file)

    if success:
        print(f"\n成功！现在可以使用密钥连接到 {args.alias}")
        sys.exit(0)
    else:
        print(f"\n失败: 无法迁移 {args.alias}")
        sys.exit(1)


if __name__ == '__main__':
    main()
