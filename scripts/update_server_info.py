#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.8"
# dependencies = ["paramiko"]
# ///
"""
批量更新服务器系统信息到 environment 字段

获取每台服务器的：操作系统/CPU核心数/内存/磁盘总空间
并更新到 SSH config 的 environment 注释字段
"""

import sys
import os
import json
import re

# 修复 Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from config_v3 import SSHConfigLoaderV3


def get_system_info(alias):
    """获取服务器系统信息"""
    try:
        # 使用智能选择创建客户端
        loader = SSHConfigLoaderV3()
        client = loader.from_alias(alias)

        # 获取操作系统
        result = client.execute("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'\"' -f2 || uname -s")
        os_name = result.stdout.strip() if result.success else "Unknown"

        # 获取 CPU 核心数
        result = client.execute("nproc 2>/dev/null || grep -c processor /proc/cpuinfo 2>/dev/null || echo '?'")
        cpu_cores = result.stdout.strip() if result.success else "?"

        # 获取内存（GB）
        result = client.execute("grep MemTotal /proc/meminfo 2>/dev/null | grep -o '[0-9]*' | head -1")
        if result.success and result.stdout.strip():
            mem_kb = int(result.stdout.strip())
            mem_gb = round(mem_kb / 1024 / 1024, 1)
            memory = f"{mem_gb}G"
        else:
            memory = "?"

        # 获取磁盘总空间
        result = client.execute("df -h / 2>/dev/null | tail -1 | tr -s ' ' | cut -d' ' -f2")
        disk = result.stdout.strip() if result.success else "?"

        # 格式化信息
        info = f"{os_name}/{cpu_cores}核/{memory}内存/{disk}磁盘"
        return {"success": True, "info": info}

    except Exception as e:
        return {"success": False, "error": str(e)}


def update_environment_field(alias, system_info):
    """更新 SSH config 中的 environment 字段"""
    config_path = os.path.expanduser("~/.ssh/config")

    if not os.path.exists(config_path):
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
        return False

    # 向前查找 environment 注释行
    env_index = -1
    for i in range(host_index - 1, max(0, host_index - 20), -1):
        line = lines[i].strip()
        if line.startswith('# environment:'):
            env_index = i
            break
        if line.startswith('# ====='):
            break

    # 更新或添加 environment 字段
    if env_index != -1:
        # 已存在，更新
        old_env = lines[env_index].strip()[14:].strip()  # 移除 "# environment: "
        if old_env:
            new_env = f"{old_env} | {system_info}"
        else:
            new_env = system_info
        lines[env_index] = f"# environment: {new_env}\n"
    else:
        # 不存在，在 Host 行前添加
        lines.insert(host_index, f"# environment: {system_info}\n")

    # 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    return True


def main():
    # 获取所有服务器列表
    loader = SSHConfigLoaderV3()

    # 读取所有 Host
    config_path = loader.config_path
    if not os.path.exists(config_path):
        print(json.dumps({"success": False, "error": "SSH config 文件不存在"}, ensure_ascii=False))
        return

    hosts = []
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('Host ') and not stripped.startswith('Host *'):
                match = re.match(r'Host\s+(.+)', stripped)
                if match:
                    alias = match.group(1).strip()
                    if '*' not in alias and '?' not in alias:
                        hosts.append(alias)

    print(f"找到 {len(hosts)} 台服务器，开始获取系统信息...\n")

    results = []
    for i, alias in enumerate(hosts, 1):
        print(f"[{i}/{len(hosts)}] 正在处理 {alias}...", end=" ")

        # 获取系统信息
        result = get_system_info(alias)

        if result["success"]:
            info = result["info"]
            print(f"✓ {info}")

            # 更新配置文件
            if update_environment_field(alias, info):
                results.append({"alias": alias, "success": True, "info": info})
            else:
                results.append({"alias": alias, "success": False, "error": "更新配置失败"})
        else:
            error = result.get("error", "未知错误")
            print(f"✗ {error}")
            results.append({"alias": alias, "success": False, "error": error})

    # 输出统计
    success_count = sum(1 for r in results if r["success"])
    print(f"\n完成！成功: {success_count}/{len(hosts)}")

    # 输出 JSON 结果
    print(json.dumps({
        "success": True,
        "total": len(hosts),
        "successful": success_count,
        "failed": len(hosts) - success_count,
        "results": results
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
