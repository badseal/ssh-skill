#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH文件上传CLI工具 v3.1

支持通过别名上传文件，SFTP 高级功能：断点续传、目录递归上传、进度显示

用法：
    python ssh_upload.py <alias> <local_path> <remote_path> [options]

示例：
    # 上传单个文件
    python ssh_upload.py prod-web-01 ./app.tar.gz /tmp/

    # 断点续传（大文件推荐）
    python ssh_upload.py prod-web-01 ./large-file.iso /tmp/ --resume

    # 上传整个目录
    python ssh_upload.py prod-web-01 ./dist/ /var/www/html/ --recursive

    # 上传目录 + 断点续传
    python ssh_upload.py prod-web-01 ./data/ /opt/data/ --recursive --resume
"""

import sys
import os
import json
import argparse
import re
import time
from typing import TextIO

# 添加lib到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from output_limits import ProgressEmitter


def _fix_remote_path(path):
    """修复被 MSYS bash 转换的远程路径（Windows 环境）"""
    # 检测 MSYS 路径转换：X:/... 或 X:\...
    if re.match(r'^[A-Za-z]:[/\\]', path):
        print(json.dumps({
            'success': False,
            'error': f'Remote path looks like a Windows path (MSYS conversion): {path}. '
                     f'Use MSYS_NO_PATHCONV=1 prefix or quote the path.'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    return path


def make_progress_callback(
    explicit: bool | None,
    *,
    stream: TextIO = sys.stderr,
    clock=time.monotonic,
):
    emitter = ProgressEmitter(stream, enabled=explicit, clock=clock)
    if not emitter.enabled:
        return None

    def callback(progress):
        emitter.emit(
            'upload',
            progress.transferred_bytes,
            progress.total_bytes,
            file_path=progress.file_path,
        )

    return callback


def _legacy_main(argv=None):
    parser = argparse.ArgumentParser(description='SSH file upload tool v3.1')
    parser.add_argument('alias', help='SSH host alias from ~/.ssh/config')
    parser.add_argument('local_path', help='Local file or directory path')
    parser.add_argument('remote_path', help='Remote file or directory path')
    parser.add_argument('--resume', action='store_true',
                        help='Enable resume for interrupted transfers')
    parser.add_argument('--recursive', action='store_true',
                        help='Upload directory recursively')
    progress_group = parser.add_mutually_exclusive_group()
    progress_group.add_argument('--progress', dest='progress', action='store_true',
                                help='Enable bounded JSONL progress on stderr')
    progress_group.add_argument('--no-progress', dest='progress', action='store_false',
                                help='Disable progress output')
    parser.set_defaults(progress=None)

    args = parser.parse_args(argv)
    remote_path = _fix_remote_path(args.remote_path)

    try:
        # 加载配置
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        params = loader.get_connection_params(args.alias)

        has_key = params.get('key_file') is not None
        has_password = params.get('password') is not None

        # 检查文件大小，大文件（>80MB）使用 Paramiko SFTP 以显示进度
        local_path_abs = os.path.abspath(args.local_path)
        is_large_file = False
        if os.path.isfile(local_path_abs):
            file_size_mb = os.path.getsize(local_path_abs) / (1024 * 1024)
            is_large_file = file_size_mb > 80  # 80MB 阈值

        # 智能选择：密钥认证且不需要高级功能且文件不大时，使用原生 SSH
        # 大文件、断点续传、递归上传、密码认证时使用 Paramiko SFTP
        use_native = has_key and not has_password and not args.resume and not args.recursive and not is_large_file

        if use_native:
            # 使用原生 SSH（简单上传，性能更好）
            client = loader.from_alias(args.alias)
            local_path = os.path.abspath(args.local_path)

            if not os.path.exists(local_path):
                print(json.dumps({
                    'success': False,
                    'error': f'Path not found: {args.local_path}'
                }, ensure_ascii=True, indent=2), file=sys.stderr)
                sys.exit(1)

            result = client.upload(local_path, remote_path, show_progress=args.progress is True)
            print(json.dumps({
                'success': result.success,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'exit_code': result.exit_code
            }, ensure_ascii=True, indent=2))
            sys.exit(0 if result.success else 1)
        else:
            # 使用 Paramiko SFTP（支持断点续传、递归上传等高级功能）
            from paramiko_client import ParamikoClient
            client = ParamikoClient(
                host=params['hostname'],
                user=params['user'],
                port=params['port'],
                password=params.get('password'),
                key_file=params.get('key_file'),
                timeout=30,
                transfer_timeout=None  # 大文件传输不设超时限制
            )

            # 获取 SSH 连接和 SFTP
            ssh_client = client._get_connection()
            sftp = ssh_client.open_sftp()

            # 设置 SFTP 超时（大文件传输使用无限制）
            sftp.get_channel().settimeout(None)

            # 创建传输器
            from sftp_transfer import SFTPTransfer
            cb = make_progress_callback(args.progress)
            transfer = SFTPTransfer(sftp, progress_callback=cb)

            local_path = os.path.abspath(args.local_path)

        # 判断是文件还是目录
        if os.path.isdir(local_path):
            if not args.recursive:
                print(json.dumps({
                    'success': False,
                    'error': f'"{args.local_path}" is a directory. Use --recursive to upload directories.'
                }, ensure_ascii=True, indent=2), file=sys.stderr)
                sys.exit(1)
            result = transfer.upload_directory(local_path, remote_path,
                                               resume=args.resume)
        elif os.path.isfile(local_path):
            result = transfer.upload_file(local_path, remote_path,
                                          resume=args.resume)
        else:
            print(json.dumps({
                'success': False,
                'error': f'Path not found: {args.local_path}'
            }, ensure_ascii=True, indent=2), file=sys.stderr)
            sys.exit(1)

        # 关闭 SFTP
        sftp.close()

        # 输出结果
        output = result.to_dict()
        print(json.dumps(output, ensure_ascii=True, indent=2))
        sys.exit(0 if result.success else 1)

    except FileNotFoundError as e:
        print(json.dumps({
            'success': False,
            'error': f'File not found: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({
            'success': False,
            'error': f'Invalid alias: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': f'Upload error: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)


def main(argv=None):
    from ssh_skill import delegate_legacy_entrypoint

    arguments = sys.argv[1:] if argv is None else list(argv)
    return delegate_legacy_entrypoint(
        'upload', arguments, legacy_main=_legacy_main
    )


if __name__ == '__main__':
    raise SystemExit(main())
