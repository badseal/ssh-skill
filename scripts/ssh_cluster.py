#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.8"
# dependencies = ["paramiko"]
# ///
"""
SSH批量操作CLI工具 v3.0

从 SSH config 读取服务器列表，支持按环境/别名过滤

用法：
    uv run ssh_cluster.py <command> [--parallel] [--hosts HOSTS] [--environment ENV]

示例：
    # 对所有服务器执行命令
    uv run ssh_cluster.py "uptime" --parallel

    # 对指定别名列表执行
    uv run ssh_cluster.py "df -h" --hosts "DEV-002,DEV-003" --parallel

    # 按环境过滤
    uv run ssh_cluster.py "uptime" --environment production --parallel

    # 健康检查
    uv run ssh_cluster.py "systemctl status nginx" --parallel --health-check
"""

import sys
import os
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from cluster import SSHCluster


def main():
    parser = argparse.ArgumentParser(description='SSH批量操作工具 v3.0')
    parser.add_argument('command', help='要执行的命令')
    parser.add_argument('--hosts', help='指定别名列表（逗号分隔）')
    parser.add_argument('--environment', help='按环境过滤')
    parser.add_argument('--tags', help='按标签过滤（逗号分隔）')
    parser.add_argument('--parallel', action='store_true', help='并发执行')
    parser.add_argument('--timeout', type=int, help='超时时间（秒）')
    parser.add_argument('--health-check', action='store_true', help='健康检查模式')
    parser.add_argument('--max-workers', type=int, default=10, help='最大并发数')

    args = parser.parse_args()

    try:
        # 解析参数
        aliases = args.hosts.split(',') if args.hosts else None
        tags = args.tags.split(',') if args.tags else None

        # 加载集群
        cluster = SSHCluster.from_ssh_config(
            aliases=aliases,
            environment=args.environment,
            tags=tags,
            max_workers=args.max_workers
        )

        if not cluster.clients:
            print(json.dumps({
                'success': False,
                'error': 'No servers matched the filter criteria'
            }, ensure_ascii=True, indent=2), file=sys.stderr)
            sys.exit(1)

        if args.health_check:
            health = cluster.health_check_all(
                check_command=args.command,
                parallel=args.parallel,
                timeout=args.timeout
            )

            output = {
                'success': True,
                'total': len(health),
                'healthy': sum(1 for v in health.values() if v),
                'unhealthy': sum(1 for v in health.values() if not v),
                'results': {name: {'healthy': status} for name, status in health.items()}
            }

            print(json.dumps(output, ensure_ascii=True, indent=2))
            sys.exit(0 if all(health.values()) else 1)

        else:
            results = cluster.execute_all(
                args.command,
                parallel=args.parallel,
                timeout=args.timeout
            )

            output = {
                'success': all(r.success for r in results.values()),
                'total': len(results),
                'successful': sum(1 for r in results.values() if r.success),
                'failed': sum(1 for r in results.values() if not r.success),
                'results': {
                    name: {
                        'success': result.success,
                        'exit_code': result.exit_code,
                        'stdout': result.stdout,
                        'stderr': result.stderr
                    }
                    for name, result in results.items()
                }
            }

            print(json.dumps(output, ensure_ascii=True, indent=2))
            sys.exit(0 if all(r.success for r in results.values()) else 1)

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
