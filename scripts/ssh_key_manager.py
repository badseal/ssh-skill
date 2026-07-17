#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.8"
# dependencies = ["paramiko"]
# ///
"""
SSH 密钥管理工具 v1.0

功能：
- 自动检测服务器类型（ESXi、Linux、FIPS模式等）
- 安全添加公钥（备份、去重、格式验证）
- 智能适配不同系统
- 批量操作支持
- 错误处理和回滚

用法：
    # 单台服务器添加密钥
    uv run ssh_key_manager.py add --host esxi-01 --key ~/.ssh/id_ed25519.pub

    # 批量添加
    uv run ssh_key_manager.py add --hosts "esxi-01,mgmt-01,dev-001" --key ~/.ssh/id_ed25519.pub

    # 所有服务器
    uv run ssh_key_manager.py add --all --key ~/.ssh/id_ed25519.pub

    # 验证密钥
    uv run ssh_key_manager.py verify --host esxi-01 --key ~/.ssh/id_ed25519.pub

    # 回滚操作
    uv run ssh_key_manager.py rollback --host esxi-01

作者：张阳 (zhangyang@bjued.cn)
日期：2026-03-04
"""

import sys
import os
import json
import argparse
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

# 添加lib到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from config_v3 import SSHConfigLoaderV3


@dataclass
class ServerInfo:
    """服务器信息"""
    alias: str
    server_type: str  # standard, esxi, fips
    auth_keys_path: str
    supports_ed25519: bool
    os_info: str


@dataclass
class SSHResult:
    """SSH命令执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class OperationResult:
    """操作结果"""
    alias: str
    success: bool
    action: str  # added, exists, skipped, failed
    message: str
    backup_file: Optional[str] = None
    error: Optional[str] = None


class SSHKeyManager:
    """SSH 密钥管理器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化密钥管理器

        Args:
            config_path: SSH config 文件路径
        """
        self.config_loader = SSHConfigLoaderV3(config_path)
        self.progress_file = os.path.expanduser("~/.ssh_key_manager_progress.json")
        self.ssh_execute_script = os.path.join(_script_dir, "ssh_execute.py")

    def detect_server_type(self, alias: str) -> ServerInfo:
        """
        检测服务器类型

        Args:
            alias: 服务器别名

        Returns:
            ServerInfo 对象
        """
        # 获取系统信息
        result = self._execute_command(alias, "uname -a 2>/dev/null || echo 'Unknown'")
        os_info = result.stdout.strip() if result.success else "Unknown"

        # 检测 ESXi
        is_esxi = "VMware ESXi" in os_info or "vmkernel" in os_info.lower()

        # 检测 FIPS 模式
        fips_result = self._execute_command(
            alias,
            "cat /proc/sys/crypto/fips_enabled 2>/dev/null || echo '0'"
        )
        is_fips = fips_result.stdout.strip() == "1" if fips_result.success else False

        # 确定服务器类型
        if is_esxi:
            server_type = "esxi"
            # ESXi 使用特殊路径
            user_result = self._execute_command(alias, "whoami 2>/dev/null || echo 'root'")
            user = user_result.stdout.strip() if user_result.success else "root"
            auth_keys_path = f"/etc/ssh/keys-{user}/authorized_keys"
            supports_ed25519 = False  # ESXi FIPS 模式不支持 ED25519
        elif is_fips:
            server_type = "fips"
            auth_keys_path = "~/.ssh/authorized_keys"
            supports_ed25519 = False  # FIPS 模式不支持 ED25519
        else:
            server_type = "standard"
            auth_keys_path = "~/.ssh/authorized_keys"
            supports_ed25519 = True

        return ServerInfo(
            alias=alias,
            server_type=server_type,
            auth_keys_path=auth_keys_path,
            supports_ed25519=supports_ed25519,
            os_info=os_info
        )

    def _execute_command(self, alias: str, command: str, timeout: int = 30) -> SSHResult:
        """
        执行 SSH 命令

        Args:
            alias: 服务器别名
            command: 要执行的命令
            timeout: 超时时间

        Returns:
            结果字典 {success, stdout, stderr, exit_code}
        """
        try:
            import subprocess

            cmd = [
                "python",
                self.ssh_execute_script,
                alias,
                command,
                "--timeout",
                str(timeout)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )

            # 解析 JSON 输出
            try:
                output = json.loads(result.stdout)
                return SSHResult(
                    success=output.get('success', False),
                    stdout=output.get('stdout', ''),
                    stderr=output.get('stderr', ''),
                    exit_code=output.get('exit_code', result.returncode)
                )
            except json.JSONDecodeError:
                return SSHResult(
                    success=result.returncode == 0,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode
                )

        except Exception as e:
            return SSHResult(
                success=False,
                stdout='',
                stderr=str(e),
                exit_code=255
            )

    def backup_authorized_keys(self, alias: str, server_info: ServerInfo) -> Optional[str]:
        """
        备份 authorized_keys 文件

        Args:
            alias: 服务器别名
            server_info: 服务器信息

        Returns:
            备份文件路径，失败返回 None
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{server_info.auth_keys_path}.backup_{timestamp}"

        result = self._execute_command(
            alias,
            f"cp {server_info.auth_keys_path} {backup_path} 2>/dev/null && echo 'OK' || echo 'FAIL'"
        )

        if result.success and "OK" in result.stdout:
            return backup_path
        return None

    def check_key_exists(self, alias: str, server_info: ServerInfo, public_key: str) -> bool:
        """
        检查密钥是否已存在

        Args:
            alias: 服务器别名
            server_info: 服务器信息
            public_key: 公钥内容

        Returns:
            True 如果密钥已存在
        """
        # 提取公钥的关键部分（去掉注释）
        key_parts = public_key.strip().split()
        if len(key_parts) < 2:
            return False

        key_signature = key_parts[1][:50]  # 取前50个字符作为特征

        result = self._execute_command(
            alias,
            f"grep -F '{key_signature}' {server_info.auth_keys_path} 2>/dev/null"
        )

        return result.success and result.exit_code == 0

    def ensure_newline(self, alias: str, server_info: ServerInfo) -> bool:
        """
        确保 authorized_keys 文件末尾有换行符

        Args:
            alias: 服务器别名
            server_info: 服务器信息

        Returns:
            True 如果成功
        """
        result = self._execute_command(
            alias,
            f"sed -i -e '$a\\' {server_info.auth_keys_path} 2>/dev/null && echo 'OK' || echo 'FAIL'"
        )

        return result.success and "OK" in result.stdout

    def verify_format(self, alias: str, server_info: ServerInfo) -> Tuple[bool, str]:
        """
        验证 authorized_keys 文件格式

        Args:
            alias: 服务器别名
            server_info: 服务器信息

        Returns:
            (是否正确, 错误信息)
        """
        # 检查是否有密钥连在一起
        result = self._execute_command(
            alias,
            f"grep '@.*ssh-' {server_info.auth_keys_path} 2>/dev/null"
        )

        if result.success and result.exit_code == 0:
            return False, "发现密钥格式错误：密钥连在一起"

        return True, ""

    def add_key(self, alias: str, public_key_content: str) -> OperationResult:
        """
        添加公钥到服务器

        Args:
            alias: 服务器别名
            public_key_content: 公钥内容

        Returns:
            OperationResult 对象
        """
        try:
            # 1. 检测服务器类型
            print(f"  检测服务器类型...")
            server_info = self.detect_server_type(alias)
            print(f"  服务器类型: {server_info.server_type}")

            # 2. 检查密钥类型兼容性
            key_type = public_key_content.split()[0] if public_key_content.strip() else ""
            if key_type == "ssh-ed25519" and not server_info.supports_ed25519:
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="skipped",
                    message=f"服务器不支持 ED25519 密钥 ({server_info.server_type} 模式)",
                    error="密钥类型不兼容"
                )

            # 3. 检查密钥是否已存在
            print(f"  检查密钥是否已存在...")
            if self.check_key_exists(alias, server_info, public_key_content):
                return OperationResult(
                    alias=alias,
                    success=True,
                    action="exists",
                    message="密钥已存在"
                )

            # 4. 备份原文件
            print(f"  备份 authorized_keys...")
            backup_file = self.backup_authorized_keys(alias, server_info)
            if not backup_file:
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="failed",
                    message="备份失败",
                    error="无法创建备份文件"
                )

            # 5. 确保文件末尾有换行符
            print(f"  确保文件格式正确...")
            self.ensure_newline(alias, server_info)

            # 6. 添加密钥
            print(f"  添加公钥...")
            escaped_key = public_key_content.replace('"', '\\"').replace('$', '\\$')
            result = self._execute_command(
                alias,
                f'echo "{escaped_key}" >> {server_info.auth_keys_path} && echo "OK" || echo "FAIL"'
            )

            if not result.success or "FAIL" in result.stdout:
                # 回滚
                self._execute_command(alias, f"cp {backup_file} {server_info.auth_keys_path}")
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="failed",
                    message="添加密钥失败",
                    error=result.stderr,
                    backup_file=backup_file
                )

            # 7. 验证格式
            print(f"  验证格式...")
            is_valid, error_msg = self.verify_format(alias, server_info)
            if not is_valid:
                # 回滚
                self._execute_command(alias, f"cp {backup_file} {server_info.auth_keys_path}")
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="failed",
                    message="格式验证失败",
                    error=error_msg,
                    backup_file=backup_file
                )

            # 8. 设置正确的权限
            self._execute_command(alias, f"chmod 600 {server_info.auth_keys_path}")

            return OperationResult(
                alias=alias,
                success=True,
                action="added",
                message=f"成功添加密钥 ({key_type})",
                backup_file=backup_file
            )

        except Exception as e:
            return OperationResult(
                alias=alias,
                success=False,
                action="failed",
                message="操作失败",
                error=str(e)
            )

    def verify_key(self, alias: str, public_key_content: str) -> OperationResult:
        """
        验证密钥是否存在

        Args:
            alias: 服务器别名
            public_key_content: 公钥内容

        Returns:
            OperationResult 对象
        """
        try:
            server_info = self.detect_server_type(alias)
            exists = self.check_key_exists(alias, server_info, public_key_content)

            if exists:
                return OperationResult(
                    alias=alias,
                    success=True,
                    action="verified",
                    message="密钥存在"
                )
            else:
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="not_found",
                    message="密钥不存在"
                )

        except Exception as e:
            return OperationResult(
                alias=alias,
                success=False,
                action="failed",
                message="验证失败",
                error=str(e)
            )

    def rollback(self, alias: str, backup_file: Optional[str] = None) -> OperationResult:
        """
        回滚到备份文件

        Args:
            alias: 服务器别名
            backup_file: 备份文件路径，如果为 None 则使用最新的备份

        Returns:
            OperationResult 对象
        """
        try:
            server_info = self.detect_server_type(alias)

            # 如果没有指定备份文件，查找最新的
            if not backup_file:
                result = self._execute_command(
                    alias,
                    f"ls -t {server_info.auth_keys_path}.backup_* 2>/dev/null | head -1"
                )
                if not result.success or not result.stdout.strip():
                    return OperationResult(
                        alias=alias,
                        success=False,
                        action="failed",
                        message="未找到备份文件"
                    )
                backup_file = result.stdout.strip()

            # 恢复备份
            result = self._execute_command(
                alias,
                f"cp {backup_file} {server_info.auth_keys_path} && echo 'OK' || echo 'FAIL'"
            )

            if result.success and "OK" in result.stdout:
                return OperationResult(
                    alias=alias,
                    success=True,
                    action="rollback",
                    message=f"已回滚到 {backup_file}"
                )
            else:
                return OperationResult(
                    alias=alias,
                    success=False,
                    action="failed",
                    message="回滚失败",
                    error=result.stderr
                )

        except Exception as e:
            return OperationResult(
                alias=alias,
                success=False,
                action="failed",
                message="回滚失败",
                error=str(e)
            )

    def batch_add_keys(
        self,
        hosts: List[str],
        public_key_content: str,
        on_error: str = "continue",
        quiet: bool = False
    ) -> List[OperationResult]:
        """
        批量添加密钥

        Args:
            hosts: 服务器别名列表
            public_key_content: 公钥内容
            on_error: 错误处理策略 (continue, stop, ask)
            quiet: 简洁模式

        Returns:
            操作结果列表
        """
        results = []
        total = len(hosts)

        for i, host in enumerate(hosts):
            if not quiet:
                print(f"\n[{i+1}/{total}] 处理 {host}...")
            else:
                print(f"[{i+1}/{total}] {host}...", end=" ", flush=True)

            result = self.add_key(host, public_key_content)
            results.append(result)

            # 保存进度
            self._save_progress(host)

            # 输出结果
            if quiet:
                if result.success:
                    if result.action == "exists":
                        print("[OK] 已存在")
                    elif result.action == "skipped":
                        print(f"[SKIP] 跳过 ({result.message})")
                    else:
                        print("[OK] 成功")
                else:
                    print(f"[FAIL] 失败")
            else:
                if result.success:
                    print(f"  [OK] {result.message}")
                else:
                    print(f"  [FAIL] {result.message}")
                    if result.error:
                        print(f"     错误: {result.error}")

            # 错误处理
            if not result.success and on_error == "stop":
                print(f"\n遇到错误，停止执行")
                break
            elif not result.success and on_error == "ask":
                response = input(f"\n继续处理剩余服务器? (y/n): ")
                if response.lower() != 'y':
                    break

        return results

    def _save_progress(self, host: str):
        """保存进度"""
        try:
            progress = self._load_progress()
            progress.append(host)
            with open(self.progress_file, 'w') as f:
                json.dump(progress, f)
        except Exception:
            pass

    def _load_progress(self) -> List[str]:
        """加载进度"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _clear_progress(self):
        """清除进度"""
        try:
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
        except Exception:
            pass

    def get_all_hosts(self) -> List[str]:
        """获取所有服务器别名"""
        try:
            config_path = os.path.expanduser("~/.ssh/config")
            if not os.path.exists(config_path):
                return []

            hosts = []
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('Host ') and not line.startswith('Host *'):
                        match = re.match(r'Host\s+(.+)', line)
                        if match:
                            host = match.group(1).strip()
                            hosts.append(host)
            return hosts
        except Exception:
            return []


def generate_summary(results: List[OperationResult]) -> str:
    """
    生成操作汇总报告

    Args:
        results: 操作结果列表

    Returns:
        汇总报告文本
    """
    total = len(results)
    success_count = sum(1 for r in results if r.success)
    failed_count = total - success_count

    added = [r for r in results if r.action == "added"]
    exists = [r for r in results if r.action == "exists"]
    skipped = [r for r in results if r.action == "skipped"]
    failed = [r for r in results if not r.success]

    report = [
        "\n" + "=" * 50,
        "批量密钥添加报告",
        "=" * 50,
        f"总计: {total} 台服务器",
        f"成功: {success_count} 台",
        f"失败: {failed_count} 台",
        ""
    ]

    if added:
        report.append(f"新添加 ({len(added)}台):")
        for r in added:
            report.append(f"  [OK] {r.alias} - {r.message}")
        report.append("")

    if exists:
        report.append(f"已存在 ({len(exists)}台):")
        for r in exists:
            report.append(f"  [EXISTS] {r.alias}")
        report.append("")

    if skipped:
        report.append(f"跳过 ({len(skipped)}台):")
        for r in skipped:
            report.append(f"  [SKIP] {r.alias} - {r.message}")
        report.append("")

    if failed:
        report.append(f"失败 ({len(failed)}台):")
        for r in failed:
            report.append(f"  [FAIL] {r.alias} - {r.message}")
            if r.error:
                report.append(f"     错误: {r.error}")
        report.append("")

    report.append("=" * 50)

    return "\n".join(report)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="SSH 密钥管理工具 - 安全、智能地管理服务器公钥",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单台服务器添加密钥
  %(prog)s add --host esxi-01 --key ~/.ssh/id_ed25519.pub

  # 批量添加
  %(prog)s add --hosts "esxi-01,mgmt-01,dev-001" --key ~/.ssh/id_ed25519.pub

  # 所有服务器
  %(prog)s add --all --key ~/.ssh/id_ed25519.pub

  # 验证密钥
  %(prog)s verify --host esxi-01 --key ~/.ssh/id_ed25519.pub

  # 回滚操作
  %(prog)s rollback --host esxi-01
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='命令')

    # add 命令
    add_parser = subparsers.add_parser('add', help='添加公钥')
    add_parser.add_argument('--host', help='服务器别名')
    add_parser.add_argument('--hosts', help='服务器别名列表（逗号分隔）')
    add_parser.add_argument('--all', action='store_true', help='所有服务器')
    add_parser.add_argument('--key', required=True, help='公钥文件路径')
    add_parser.add_argument('--on-error', choices=['continue', 'stop', 'ask'],
                           default='continue', help='错误处理策略')
    add_parser.add_argument('--quiet', action='store_true', help='简洁模式')
    add_parser.add_argument('--resume', action='store_true', help='从上次中断处继续')

    # verify 命令
    verify_parser = subparsers.add_parser('verify', help='验证密钥')
    verify_parser.add_argument('--host', required=True, help='服务器别名')
    verify_parser.add_argument('--key', required=True, help='公钥文件路径')

    # rollback 命令
    rollback_parser = subparsers.add_parser('rollback', help='回滚操作')
    rollback_parser.add_argument('--host', required=True, help='服务器别名')
    rollback_parser.add_argument('--backup', help='备份文件路径')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    manager = SSHKeyManager()

    try:
        if args.command == 'add':
            # 读取公钥文件
            key_path = os.path.expanduser(args.key)
            if not os.path.exists(key_path):
                print(f"错误: 公钥文件不存在: {key_path}")
                return 1

            with open(key_path, 'r') as f:
                public_key = f.read().strip()

            # 确定目标服务器列表
            if args.all:
                hosts = manager.get_all_hosts()
                if not hosts:
                    print("错误: 未找到任何服务器配置")
                    return 1
                print(f"找到 {len(hosts)} 台服务器")
            elif args.hosts:
                hosts = [h.strip() for h in args.hosts.split(',')]
            elif args.host:
                hosts = [args.host]
            else:
                print("错误: 必须指定 --host, --hosts 或 --all")
                return 1

            # 处理断点续传
            if args.resume:
                completed = manager._load_progress()
                if completed:
                    print(f"从上次中断处继续，已完成 {len(completed)} 台")
                    hosts = [h for h in hosts if h not in completed]
                    if not hosts:
                        print("所有服务器已处理完成")
                        manager._clear_progress()
                        return 0

            # 执行批量添加
            if len(hosts) == 1:
                # 单台服务器
                print(f"添加公钥到 {hosts[0]}...")
                result = manager.add_key(hosts[0], public_key)
                if result.success:
                    print(f"[OK] {result.message}")
                    return 0
                else:
                    print(f"[FAIL] {result.message}")
                    if result.error:
                        print(f"错误: {result.error}")
                    return 1
            else:
                # 批量处理
                start_time = time.time()
                results = manager.batch_add_keys(
                    hosts, public_key, args.on_error, args.quiet
                )
                elapsed = time.time() - start_time

                # 生成报告
                print(generate_summary(results))
                print(f"用时: {elapsed:.1f} 秒")

                # 清除进度
                manager._clear_progress()

                # 返回状态
                return 0 if all(r.success for r in results) else 1

        elif args.command == 'verify':
            # 读取公钥文件
            key_path = os.path.expanduser(args.key)
            if not os.path.exists(key_path):
                print(f"错误: 公钥文件不存在: {key_path}")
                return 1

            with open(key_path, 'r') as f:
                public_key = f.read().strip()

            result = manager.verify_key(args.host, public_key)
            if result.success:
                print(f"[OK] {result.message}")
                return 0
            else:
                print(f"[FAIL] {result.message}")
                return 1

        elif args.command == 'rollback':
            result = manager.rollback(args.host, args.backup)
            if result.success:
                print(f"[OK] {result.message}")
                return 0
            else:
                print(f"[FAIL] {result.message}")
                if result.error:
                    print(f"错误: {result.error}")
                return 1

    except KeyboardInterrupt:
        print("\n\n操作已取消")
        return 130
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
