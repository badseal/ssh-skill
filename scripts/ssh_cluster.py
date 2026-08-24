#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH批量操作CLI工具 v3.0

从 SSH config 读取服务器列表，支持按环境/别名过滤

用法：
    python ssh_cluster.py <command> [--parallel] [--hosts HOSTS] [--environment ENV]

示例：
    # 对所有服务器执行命令
    python ssh_cluster.py "uptime" --parallel

    # 对指定别名列表执行
    python ssh_cluster.py "df -h" --hosts "DEV-002,DEV-003" --parallel

    # 按环境过滤
    python ssh_cluster.py "uptime" --environment production --parallel

    # 健康检查
    python ssh_cluster.py "systemctl status nginx" --parallel --health-check
"""

import sys
import os
import json
import argparse
from typing import Sequence, TextIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

from cluster import SSHCluster
from cluster_plan import ConfirmationRequired, resolve_cluster_plan, validate_cluster_apply
from config_v3 import SSHConfigLoaderV3
from result_protocol import error_result, exit_code_for, success_result, write_result


def _execution_result_data(result) -> dict:
    data = {
        'success': result.success,
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
    }
    for key in ('error_code', 'retryable', 'outcome'):
        value = getattr(result, key, None)
        if value is not None:
            data[key] = value
    return data


def _health_result_data(result) -> dict:
    data = {'healthy': result.success}
    for key in ('error_code', 'retryable', 'outcome'):
        value = getattr(result, key, None)
        if value is not None:
            data[key] = value
    return data


def main(
    argv: Sequence[str] | None = None,
    *,
    loader=None,
    stdout: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(description='SSH批量操作工具 v3.0')
    parser.add_argument('command', help='要执行的命令')
    parser.add_argument('--hosts', help='指定别名列表（逗号分隔）')
    parser.add_argument('--environment', help='按环境过滤')
    parser.add_argument('--tags', help='按标签过滤（逗号分隔）')
    parser.add_argument('--parallel', action='store_true', help='并发执行')
    parser.add_argument('--timeout', type=int, help='超时时间（秒）')
    parser.add_argument('--health-check', action='store_true', help='健康检查模式')
    parser.add_argument('--max-workers', type=int, default=10, help='最大并发数')
    parser.add_argument('--apply', action='store_true', help='确认执行远程操作')
    parser.add_argument('--confirm-production', action='store_true',
                        help='二次确认允许操作生产目标')

    args = parser.parse_args(argv)

    try:
        aliases = args.hosts.split(',') if args.hosts else None
        tags = args.tags.split(',') if args.tags else None

        loader = loader or SSHConfigLoaderV3()
        plan = resolve_cluster_plan(loader, aliases, args.environment, tags)
        plan_data = plan.to_dict()

        if not args.apply:
            write_result(
                success_result("cluster", {"mode": "preview", **plan_data}),
                stream=stdout,
            )
            return 0

        validate_cluster_apply(plan, args.apply, args.confirm_production)
        if not plan.targets:
            result = error_result(
                "cluster",
                code="no_targets",
                message="no servers matched the filter criteria",
                data={"mode": "apply", **plan_data},
            )
            write_result(result, stream=stdout)
            return exit_code_for(result)

        cluster = SSHCluster.from_plan(
            plan, loader=loader, max_workers=args.max_workers
        )
        if cluster.client_errors:
            result = error_result(
                "cluster",
                code="client_creation_failed",
                message="one or more target configurations could not create clients",
                data={
                    "mode": "apply",
                    **plan_data,
                    "client_errors": dict(sorted(cluster.client_errors.items())),
                },
            )
            write_result(result, stream=stdout)
            return exit_code_for(result)

        if args.health_check:
            health_results = cluster.execute_all(
                args.command,
                parallel=args.parallel,
                timeout=args.timeout
            )
            health = {name: value.success for name, value in health_results.items()}
            unknown = any(
                getattr(value, 'outcome', None) == 'unknown'
                for value in health_results.values()
            )

            result = success_result("cluster", {
                "mode": "apply",
                **plan_data,
                'total': len(health),
                'healthy': sum(1 for v in health.values() if v),
                'unhealthy': sum(1 for v in health.values() if not v),
                'results': {
                    name: _health_result_data(value)
                    for name, value in health_results.items()
                },
            })
            if unknown:
                result["success"] = False
                result["error"] = {
                    "code": "outcome_unknown",
                    "message": "one or more health check outcomes are unavailable",
                    "retryable": False,
                    "outcome": "unknown",
                }
            elif not all(health.values()):
                result["success"] = False
                result["error"] = {
                    "code": "health_check_failed",
                    "message": "one or more health checks failed",
                    "retryable": False,
                    "outcome": "failed",
                }
            write_result(result, stream=stdout)
            return exit_code_for(result)

        else:
            results = cluster.execute_all(
                args.command,
                parallel=args.parallel,
                timeout=args.timeout
            )

            result = success_result("cluster", {
                "mode": "apply",
                **plan_data,
                'total': len(results),
                'successful': sum(1 for r in results.values() if r.success),
                'failed': sum(1 for r in results.values() if not r.success),
                'results': {
                    name: _execution_result_data(value)
                    for name, value in results.items()
                },
            })
            unknown = any(
                getattr(value, 'outcome', None) == 'unknown'
                for value in results.values()
            )
            if unknown:
                result["success"] = False
                result["error"] = {
                    "code": "outcome_unknown",
                    "message": "one or more cluster operation outcomes are unavailable",
                    "retryable": False,
                    "outcome": "unknown",
                }
            elif not all(r.success for r in results.values()):
                result["success"] = False
                result["error"] = {
                    "code": "cluster_operation_failed",
                    "message": "one or more cluster operations failed",
                    "retryable": False,
                    "outcome": "failed",
                }
            write_result(result, stream=stdout)
            return exit_code_for(result)

    except ConfirmationRequired as exc:
        result = error_result(
            "cluster",
            code="confirmation_required",
            message=str(exc),
            data={"mode": "preview", **locals().get("plan_data", {})},
        )
        write_result(result, stream=stdout)
        return exit_code_for(result)

    except Exception as e:
        result = error_result(
            "cluster", code="cluster_error", message=str(e), retryable=False
        )
        write_result(result, stream=stdout)
        return exit_code_for(result)


if __name__ == '__main__':
    raise SystemExit(main())
