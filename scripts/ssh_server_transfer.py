#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 服务器间文件传输工具 v1.0

支持两种传输模式：
1. 直连模式（Direct）：在源服务器上执行 scp/rsync，数据直接在服务器间传输
2. 流式转发模式（Stream）：本地同时连接两台服务器，流式中转数据

用法：
    python ssh_server_transfer.py <源别名> <源路径> <目标别名> <目标路径> [options]

示例：
    # 自动模式（推荐）
    python ssh_server_transfer.py prod-web-01 /var/log/app.log backup-server /backup/

    # 强制直连模式（大文件推荐）
    python ssh_server_transfer.py prod-web-01 /data/large.tar.gz backup-server /backup/ --mode direct

    # 强制流式转发（小文件或网络不通时）
    python ssh_server_transfer.py prod-web-01 /etc/config.conf backup-server /backup/ --mode stream

    # 混合模式（先尝试直连，失败后自动降级到流式）
    python ssh_server_transfer.py prod-web-01 /data/ backup-server /backup/ --mode hybrid

    # 使用 rsync（仅直连模式支持）
    python ssh_server_transfer.py prod-web-01 /data/ backup-server /backup/data/ --use-rsync
"""

import sys
import os
import json
import time
import argparse
import posixpath
import re

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

from output_limits import BoundedText, ProgressEmitter, collect_text, progress_is_enabled
from security import configure_paramiko_host_keys


def _fix_remote_path(path):
    """修复被 MSYS bash 转换的远程路径（Windows 环境）"""
    if re.match(r'^[A-Za-z]:[/\\]', path):
        print(json.dumps({
            'success': False,
            'error': f'远程路径被 Windows MSYS 转换: {path}. '
                     f'请使用 MSYS_NO_PATHCONV=1 前缀或用引号包裹路径。'
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    return path


def _human_size(size_bytes):
    """将字节数转为人类可读格式"""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"


def check_ssh_agent():
    """检查本地是否有 SSH agent 运行"""
    # Unix/Linux/macOS
    if 'SSH_AUTH_SOCK' in os.environ:
        return True
    # Windows OpenSSH agent
    if os.name == 'nt':
        try:
            import subprocess
            result = subprocess.run(
                ['ssh-add', '-l'],
                capture_output=True, text=True, timeout=5
            )
            # exit code 0 = 有密钥，1 = agent 运行但无密钥
            return result.returncode in (0, 1)
        except Exception:
            pass
    return False


def get_connection_params(alias):
    """获取服务器连接参数"""
    from config_v3 import SSHConfigLoaderV3
    loader = SSHConfigLoaderV3()
    return loader.get_connection_params(alias)


def create_ssh_client(alias):
    """根据别名创建 SSH 客户端（智能选择）"""
    from config_v3 import SSHConfigLoaderV3
    loader = SSHConfigLoaderV3()
    return loader.from_alias(alias)


def get_remote_file_size(alias, remote_path):
    """获取远程文件/目录大小（字节）"""
    client = create_ssh_client(alias)
    # 先尝试文件，再尝试目录
    result = client.execute(
        f"stat -c %s '{remote_path}' 2>/dev/null || "
        f"du -sb '{remote_path}' 2>/dev/null | awk '{{print $1}}'"
    )
    if result.success and result.stdout.strip():
        try:
            return int(result.stdout.strip().split('\n')[0])
        except ValueError:
            pass
    return -1


def is_remote_directory(alias, remote_path):
    """检查远程路径是否为目录"""
    client = create_ssh_client(alias)
    result = client.execute(f"test -d '{remote_path}' && echo 'DIR' || echo 'FILE'")
    return result.success and 'DIR' in result.stdout


def can_servers_connect(source_alias, dest_alias):
    """检查源服务器是否能连接到目标服务器的 SSH 端口"""
    try:
        dest_params = get_connection_params(dest_alias)
        dest_host = dest_params['hostname']
        dest_port = dest_params['port']

        client = create_ssh_client(source_alias)
        test_cmd = (
            f"timeout 5 bash -c 'cat < /dev/null > /dev/tcp/{dest_host}/{dest_port}' "
            f"2>/dev/null && echo 'OK' || echo 'FAIL'"
        )
        result = client.execute(test_cmd)
        return result.success and 'OK' in result.stdout
    except Exception:
        return False


def choose_transfer_mode(source_alias, source_path, dest_alias, size_threshold_mb=10):
    """
    智能选择传输模式

    Returns:
        'direct', 'stream', 或 'hybrid'
    """
    # 1. 获取文件大小
    file_size = get_remote_file_size(source_alias, source_path)
    if file_size < 0:
        # 无法获取文件大小，使用流式（更安全）
        return 'stream'

    size_mb = file_size / (1024 * 1024)

    # 2. 小文件直接使用流式转发
    if size_mb < size_threshold_mb:
        return 'stream'

    # 3. 大文件检查服务器间连通性
    if can_servers_connect(source_alias, dest_alias):
        if check_ssh_agent():
            return 'direct'
        else:
            # 无 agent，降级到流式
            return 'stream'
    else:
        return 'stream'


def stream_transfer(source_alias, source_path, dest_alias, dest_path,
                    progress=True):
    """
    流式转发传输：本地同时连接两台服务器，流式读写数据

    支持单个文件传输。数据经过本地但不存储在本地。
    """
    source_params = get_connection_params(source_alias)
    dest_params = get_connection_params(dest_alias)

    source_client = create_ssh_client(source_params)
    dest_client = create_ssh_client(dest_params)

    source_ssh = source_client._get_connection()
    dest_ssh = dest_client._get_connection()

    source_sftp = source_ssh.open_sftp()
    dest_sftp = dest_ssh.open_sftp()

    try:
        # 检查源是否为目录
        source_is_dir = False
        try:
            import stat
            source_stat = source_sftp.stat(source_path)
            source_is_dir = stat.S_ISDIR(source_stat.st_mode)
        except Exception:
            pass

        if source_is_dir:
            return _stream_transfer_directory(
                source_sftp, dest_sftp,
                source_path, dest_path,
                source_alias, dest_alias,
                progress
            )
        else:
            return _stream_transfer_file(
                source_sftp, dest_sftp,
                source_path, dest_path,
                progress
            )

    finally:
        try:
            source_sftp.close()
        except Exception:
            pass
        try:
            dest_sftp.close()
        except Exception:
            pass


def _stream_transfer_file(source_sftp, dest_sftp, source_path, dest_path,
                          progress=True):
    """流式传输单个文件"""
    # 获取源文件大小
    try:
        source_stat = source_sftp.stat(source_path)
        total_size = source_stat.st_size
    except Exception as e:
        return {
            'success': False,
            'mode': 'stream',
            'error': f'无法获取源文件信息: {e}'
        }

    # 如果目标路径以 / 结尾，追加源文件名
    if dest_path.endswith('/'):
        dest_path = dest_path + posixpath.basename(source_path)

    # 确保目标目录存在
    dest_dir = posixpath.dirname(dest_path)
    if dest_dir and dest_dir != '/':
        _remote_mkdir_p(dest_sftp, dest_dir)

    start_time = time.time()
    transferred = 0
    chunk_size = 64 * 1024  # 64KB
    progress_emitter = ProgressEmitter(sys.stderr, enabled=progress)

    try:
        with source_sftp.open(source_path, 'rb') as src_file:
            with dest_sftp.open(dest_path, 'wb') as dst_file:
                dst_file.set_pipelined(True)
                while True:
                    chunk = src_file.read(chunk_size)
                    if not chunk:
                        break
                    dst_file.write(chunk)
                    transferred += len(chunk)

                    if progress_emitter.enabled:
                        elapsed = time.time() - start_time
                        speed = transferred / elapsed if elapsed > 0 else 0
                        progress_emitter.emit(
                            'transfer', transferred, total_size,
                            file_path=posixpath.basename(source_path),
                            speed=_human_size(int(speed)) + '/s',
                        )

        elapsed = time.time() - start_time
        return {
            'success': True,
            'mode': 'stream',
            'files_transferred': 1,
            'bytes_transferred': transferred,
            'bytes_human': _human_size(transferred),
            'time_elapsed': round(elapsed, 2),
            'speed': _human_size(int(transferred / elapsed)) + '/s' if elapsed > 0 else 'N/A',
        }
    except Exception as e:
        return {
            'success': False,
            'mode': 'stream',
            'bytes_transferred': transferred,
            'error': f'流式传输失败: {e}'
        }


def _stream_transfer_directory(source_sftp, dest_sftp, source_dir, dest_dir,
                               source_alias, dest_alias, progress=True):
    """流式传输目录"""
    import stat as stat_module

    # 确保目标根目录存在
    _remote_mkdir_p(dest_sftp, dest_dir)

    files_transferred = 0
    files_failed = 0
    total_bytes = 0
    errors = []
    start_time = time.time()

    def transfer_dir_recursive(src_dir, dst_dir):
        nonlocal files_transferred, files_failed, total_bytes, errors

        try:
            entries = source_sftp.listdir_attr(src_dir)
        except Exception as e:
            errors.append(f"无法列出目录 {src_dir}: {e}")
            files_failed += 1
            return

        for entry in entries:
            src_path = src_dir.rstrip('/') + '/' + entry.filename
            dst_path = dst_dir.rstrip('/') + '/' + entry.filename

            if stat_module.S_ISDIR(entry.st_mode):
                _remote_mkdir_p(dest_sftp, dst_path)
                transfer_dir_recursive(src_path, dst_path)
            else:
                result = _stream_transfer_file(
                    source_sftp, dest_sftp,
                    src_path, dst_path,
                    progress=progress
                )
                if result['success']:
                    files_transferred += 1
                    total_bytes += result.get('bytes_transferred', 0)
                else:
                    files_failed += 1
                    errors.append(result.get('error', f'传输失败: {src_path}'))

    transfer_dir_recursive(source_dir, dest_dir)

    elapsed = time.time() - start_time
    return {
        'success': files_failed == 0,
        'mode': 'stream',
        'files_transferred': files_transferred,
        'files_failed': files_failed,
        'bytes_transferred': total_bytes,
        'bytes_human': _human_size(total_bytes),
        'time_elapsed': round(elapsed, 2),
        'speed': _human_size(int(total_bytes / elapsed)) + '/s' if elapsed > 0 else 'N/A',
        'errors': errors if errors else None,
    }


def _remote_mkdir_p(sftp, remote_dir):
    """递归创建远程目录"""
    dirs_to_create = []
    current = remote_dir

    while current and current != '/':
        try:
            sftp.stat(current)
            break  # 目录存在
        except (FileNotFoundError, IOError):
            dirs_to_create.insert(0, current)
            parent = posixpath.dirname(current)
            if parent == current:
                break
            current = parent

    for d in dirs_to_create:
        try:
            sftp.mkdir(d)
        except IOError:
            pass  # 目录可能已存在


def direct_transfer(source_alias, source_path, dest_alias, dest_path,
                    use_rsync=False, progress=True, timeout=300,
                    allow_agent_forwarding=False):
    """
    直连模式传输：在源服务器上执行 scp/rsync 命令

    需要 SSH agent forwarding 或预配置密钥。
    """
    import paramiko

    dest_params = get_connection_params(dest_alias)
    source_params = get_connection_params(source_alias)
    dest_host = dest_params['hostname']
    dest_user = dest_params['user']
    dest_port = dest_params['port']

    # 构建传输命令
    if use_rsync:
        cmd = (
            f"rsync -avz --progress "
            f"-e 'ssh -p {dest_port} -o StrictHostKeyChecking=accept-new' "
            f"'{source_path}' '{dest_user}@{dest_host}:{dest_path}'"
        )
    else:
        # scp 命令
        cmd = (
            f"scp -o StrictHostKeyChecking=accept-new "
            f"-P {dest_port} "
            f"'{source_path}' '{dest_user}@{dest_host}:{dest_path}'"
        )

    # 连接源服务器；agent forwarding 仅在显式授权时启用。
    client = paramiko.SSHClient()
    configure_paramiko_host_keys(client)

    use_local_agent = not (
        source_params.get('key_file') or source_params.get('password')
    )

    connect_kwargs = {
        'hostname': source_params['hostname'],
        'port': source_params['port'],
        'username': source_params['user'],
        'timeout': 30,
        'allow_agent': use_local_agent,
        'look_for_keys': use_local_agent,
    }

    if source_params.get('key_file'):
        connect_kwargs['key_filename'] = source_params['key_file']
    if source_params.get('password'):
        connect_kwargs['password'] = source_params['password']

    client.connect(**connect_kwargs)

    start_time = time.time()
    output_collector = BoundedText()
    progress_emitter = ProgressEmitter(sys.stderr, enabled=progress)
    session = None
    agent_warnings = []

    try:
        if allow_agent_forwarding:
            session = client.get_transport().open_session()
            session.settimeout(timeout)
            try:
                paramiko.agent.AgentRequestHandler(session)
            except Exception:
                agent_warnings.append('agent_forwarding_unavailable')
            session.get_pty()
            session.exec_command(cmd)
            stdout = session.makefile('r')
            stderr = session.makefile_stderr('r')
        else:
            _, stdout, stderr = client.exec_command(
                cmd, timeout=timeout, get_pty=True
            )

        for line in stdout:
            if isinstance(line, bytes):
                line = line.decode('utf-8', errors='replace')
            stripped = line.strip()
            if stripped:
                output_collector.feed((stripped + '\n').encode('utf-8'))
                if progress_emitter.enabled:
                    # 解析进度并输出
                    progress_info = _parse_transfer_progress(stripped, use_rsync)
                    if progress_info:
                        percent = float(progress_info.get('percent', 0))
                        progress_emitter.emit(
                            'transfer', int(percent * 100), 10000,
                            **{key: value for key, value in progress_info.items()
                               if key != 'percent'},
                        )

        exit_code = stdout.channel.recv_exit_status()
        stderr_result = collect_text(stderr.read())
        output_result = output_collector.finish()
        elapsed = time.time() - start_time

        result = {
            'success': exit_code == 0,
            'mode': 'direct',
            'method': 'rsync' if use_rsync else 'scp',
            'exit_code': exit_code,
            'time_elapsed': round(elapsed, 2),
            'command': cmd,
            'output': output_result.text,
            'stderr': stderr_result.text if exit_code != 0 else None,
            'output_limits': {
                'output': output_result.to_meta(),
                'stderr': stderr_result.to_meta(),
            },
            'meta': {'warnings': agent_warnings},
        }
        if allow_agent_forwarding:
            result['meta']['warnings'].append('agent_forwarding_enabled')
        return result
    except Exception as e:
        return {
            'success': False,
            'mode': 'direct',
            'error': f'直连传输失败: {e}',
            'command': cmd,
            'meta': {'warnings': agent_warnings},
        }
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def _parse_transfer_progress(line, is_rsync=False):
    """解析 scp/rsync 进度输出"""
    if is_rsync:
        # rsync 输出格式：1,234,567  45%  2.10MB/s  0:00:30
        match = re.search(r'(\d+)%\s+(\S+/s)', line)
        if match:
            return {
                'percent': int(match.group(1)),
                'speed': match.group(2),
            }
    else:
        # scp 输出格式：filename  45%  123MB  2.1MB/s  00:30 ETA
        match = re.search(r'(\d+)%\s+(\S+)\s+(\S+/s)', line)
        if match:
            return {
                'percent': int(match.group(1)),
                'transferred': match.group(2),
                'speed': match.group(3),
            }
    return None


def validate_transfer(source_alias, dest_alias):
    """验证传输前置条件"""
    issues = []

    # 检查源服务器连接
    try:
        client = create_ssh_client(get_connection_params(source_alias))
        result = client.execute("echo OK")
        if not result.success:
            issues.append(f"无法连接到源服务器 {source_alias}: {result.stderr}")
    except Exception as e:
        issues.append(f"源服务器 {source_alias} 连接失败: {e}")

    # 检查目标服务器连接
    try:
        client = create_ssh_client(get_connection_params(dest_alias))
        result = client.execute("echo OK")
        if not result.success:
            issues.append(f"无法连接到目标服务器 {dest_alias}: {result.stderr}")
    except Exception as e:
        issues.append(f"目标服务器 {dest_alias} 连接失败: {e}")

    return issues


def server_transfer(source_alias, source_path, dest_alias, dest_path,
                    mode='auto', use_rsync=False, progress=True,
                    size_threshold_mb=10, timeout=300,
                    allow_agent_forwarding=False):
    """
    服务器到服务器文件传输（统一接口）

    Args:
        source_alias: 源服务器别名
        source_path: 源文件/目录路径
        dest_alias: 目标服务器别名
        dest_path: 目标文件/目录路径
        mode: 传输模式 (auto/direct/stream/hybrid)
        use_rsync: 是否使用 rsync（仅直连模式）
        progress: 是否显示进度
        size_threshold_mb: 大小阈值（MB）
        timeout: 超时时间（秒）

    Returns:
        传输结果字典
    """
    # 0. 验证前置条件
    issues = validate_transfer(source_alias, dest_alias)
    if issues:
        return {
            'success': False,
            'error': '前置条件检查失败',
            'issues': issues,
        }

    # 1. 确定传输模式
    if mode == 'auto':
        selected_mode = choose_transfer_mode(
            source_alias, source_path, dest_alias, size_threshold_mb
        )
        mode_reason = f"自动选择传输模式: {selected_mode}"
    elif mode == 'hybrid':
        selected_mode = 'direct'  # 先尝试直连
        mode_reason = "混合模式: 先尝试直连"
    else:
        selected_mode = mode
        mode_reason = f"强制使用模式: {selected_mode}"

    if progress:
        sys.stderr.write(json.dumps({
            'status': 'mode_selected',
            'mode': selected_mode,
            'reason': mode_reason,
        }, ensure_ascii=False) + '\n')
        sys.stderr.flush()

    # 2. 执行传输
    try:
        if selected_mode == 'direct':
            if allow_agent_forwarding and not check_ssh_agent():
                if mode == 'hybrid':
                    # 混合模式：无 agent 时降级
                    if progress:
                        sys.stderr.write(json.dumps({
                            'status': 'fallback',
                            'reason': '未检测到 SSH agent，降级到流式转发'
                        }, ensure_ascii=False) + '\n')
                        sys.stderr.flush()
                    return stream_transfer(
                        source_alias, source_path,
                        dest_alias, dest_path, progress
                    )
                elif mode == 'direct':
                    # 强制直连但无 agent，仍然尝试（可能有预配置密钥）
                    pass

            result = direct_transfer(
                source_alias, source_path,
                dest_alias, dest_path,
                use_rsync=use_rsync,
                progress=progress,
                timeout=timeout,
                allow_agent_forwarding=allow_agent_forwarding,
            )
            meta = result.setdefault('meta', {})
            warnings = meta.setdefault('warnings', [])
            if allow_agent_forwarding and 'agent_forwarding_enabled' not in warnings:
                warnings.append('agent_forwarding_enabled')
            return result

        elif selected_mode == 'stream':
            return stream_transfer(
                source_alias, source_path,
                dest_alias, dest_path, progress
            )

    except Exception as e:
        # 混合模式：直连失败后降级
        if mode == 'hybrid' and selected_mode == 'direct':
            if progress:
                sys.stderr.write(json.dumps({
                    'status': 'fallback',
                    'reason': f'直连模式失败: {e}，自动降级到流式转发'
                }, ensure_ascii=False) + '\n')
                sys.stderr.flush()
            return stream_transfer(
                source_alias, source_path,
                dest_alias, dest_path, progress
            )
        else:
            return {
                'success': False,
                'mode': selected_mode,
                'error': str(e),
            }


def _legacy_main(argv=None):
    parser = argparse.ArgumentParser(
        description='SSH 服务器间文件传输工具 v1.0'
    )
    parser.add_argument('source_alias', help='源服务器别名（来自 ~/.ssh/config）')
    parser.add_argument('source_path', help='源文件/目录路径')
    parser.add_argument('dest_alias', help='目标服务器别名')
    parser.add_argument('dest_path', help='目标文件/目录路径')
    parser.add_argument('--mode', choices=['auto', 'direct', 'stream', 'hybrid'],
                        default='auto',
                        help='传输模式 (默认: auto)')
    parser.add_argument('--use-rsync', action='store_true',
                        help='使用 rsync（仅直连模式，支持增量同步）')
    parser.add_argument('--allow-agent-forwarding', action='store_true',
                        help='显式允许将本地 SSH agent 转发到源服务器')
    progress_group = parser.add_mutually_exclusive_group()
    progress_group.add_argument('--progress', dest='progress', action='store_true',
                                help='在 stderr 启用有界 JSONL 进度')
    progress_group.add_argument('--no-progress', dest='progress', action='store_false',
                                help='禁用进度输出')
    parser.set_defaults(progress=None)
    parser.add_argument('--size-threshold', type=int, default=10,
                        help='大小阈值（MB），超过此值优先使用直连 (默认: 10)')
    parser.add_argument('--timeout', type=int, default=300,
                        help='超时时间（秒）(默认: 300)')

    args = parser.parse_args(argv)

    # 修复 MSYS 路径转换
    source_path = _fix_remote_path(args.source_path)
    dest_path = _fix_remote_path(args.dest_path)

    show_progress = progress_is_enabled(args.progress, sys.stderr)

    try:
        result = server_transfer(
            source_alias=args.source_alias,
            source_path=source_path,
            dest_alias=args.dest_alias,
            dest_path=dest_path,
            mode=args.mode,
            use_rsync=args.use_rsync,
            progress=show_progress,
            size_threshold_mb=args.size_threshold,
            timeout=args.timeout,
            allow_agent_forwarding=args.allow_agent_forwarding,
        )

        # 输出结果
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get('success') else 1)

    except FileNotFoundError as e:
        print(json.dumps({
            'success': False,
            'error': f'配置文件未找到: {e}'
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({
            'success': False,
            'error': f'无效的别名: {e}'
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': f'传输错误: {e}'
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


def main(argv=None):
    from ssh_skill import delegate_legacy_entrypoint

    arguments = sys.argv[1:] if argv is None else list(argv)
    return delegate_legacy_entrypoint(
        'transfer', arguments, legacy_main=_legacy_main
    )


if __name__ == '__main__':
    raise SystemExit(main())
