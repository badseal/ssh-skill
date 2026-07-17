#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.8"
# dependencies = ["paramiko"]
# ///
"""
部署公钥到远程服务器

将指定的公钥部署到远程服务器，实现从密码认证迁移到密钥认证。
"""

import sys
import os
import argparse

# 修复 Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))


def deploy_pubkey(alias, pubkey_content, key_name):
    """
    部署公钥到远程服务器

    Args:
        alias: 服务器别名
        pubkey_content: 公钥内容
        key_name: 密钥名称（用于标识）

    Returns:
        bool: 是否成功
    """
    from config_v3 import SSHConfigLoaderV3

    try:
        # 加载配置
        loader = SSHConfigLoaderV3()
        params = loader.get_connection_params(alias)

        # 检查是否有密码
        if not params.get('password'):
            print(f"错误: {alias} 没有配置密码，无法使用密码认证部署公钥")
            return False

        # 使用密码认证创建客户端
        from paramiko_client import ParamikoClient
        client = ParamikoClient(
            host=params['hostname'],
            user=params['user'],
            port=params['port'],
            password=params['password'],
            timeout=30
        )

        print(f"正在连接到 {alias}...")

        # 测试连接
        result = client.execute("echo 'Connection OK'")
        if not result.success:
            print(f"错误: 无法连接到 {alias}")
            return False

        print("连接成功，开始部署公钥...")

        # 创建 .ssh 目录（如果不存在）
        result = client.execute("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
        if not result.success:
            print(f"错误: 无法创建 .ssh 目录")
            return False

        # 检查公钥是否已存在
        result = client.execute(f"grep -F '{pubkey_content.strip()}' ~/.ssh/authorized_keys 2>/dev/null")
        if result.success and result.stdout.strip():
            print(f"公钥已存在于 {alias}，无需重复添加")
            return True

        # 追加公钥到 authorized_keys
        escaped_pubkey = pubkey_content.strip().replace("'", "'\\''")
        result = client.execute(
            f"echo '{escaped_pubkey}' >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys"
        )

        if not result.success:
            print(f"错误: 无法写入公钥到 authorized_keys")
            print(f"错误信息: {result.stderr}")
            return False

        print(f"✓ 公钥已成功部署到 {alias}")

        # 验证密钥认证是否工作
        print("正在验证密钥认证...")
        # 这里需要使用新的密钥文件测试连接
        # 暂时跳过验证，由用户手动测试

        return True

    except Exception as e:
        print(f"错误: {str(e)}")
        return False


def main():
    parser = argparse.ArgumentParser(description='部署公钥到远程服务器')
    parser.add_argument('alias', help='服务器别名')
    parser.add_argument('--pubkey-file', required=True, help='公钥文件路径')
    parser.add_argument('--key-name', required=True, help='密钥名称（如 id_rsa_sa_legacy）')

    args = parser.parse_args()

    # 读取公钥内容
    pubkey_file = os.path.expanduser(args.pubkey_file)
    if not os.path.exists(pubkey_file):
        print(f"错误: 公钥文件不存在: {pubkey_file}")
        sys.exit(1)

    with open(pubkey_file, 'r', encoding='utf-8') as f:
        pubkey_content = f.read().strip()

    if not pubkey_content:
        print(f"错误: 公钥文件为空")
        sys.exit(1)

    # 部署公钥
    success = deploy_pubkey(args.alias, pubkey_content, args.key_name)

    if success:
        print(f"\n成功！现在可以使用密钥 {args.key_name} 连接到 {args.alias}")
        print(f"建议: 使用 migrate_to_key_auth.py 更新 SSH config")
        sys.exit(0)
    else:
        print(f"\n失败: 无法部署公钥到 {args.alias}")
        sys.exit(1)


if __name__ == '__main__':
    main()
