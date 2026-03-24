#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH Tunnel 守护进程 v1.0

在本地维护 SSH 端口转发隧道，支持守护进程模式运行。
自动启动、空闲超时自动退出、自动重连。

用法：
    python ssh_tunnel.py start <alias> --remote-port <port> [--local-port <port>] [--remote-host <host>]
    python ssh_tunnel.py list
    python ssh_tunnel.py status <tunnel-id>
    python ssh_tunnel.py stop <tunnel-id>
    python ssh_tunnel.py stop-all <alias>

示例：
    # 转发远程 MySQL（自动分配本地端口）
    python ssh_tunnel.py start prod-db-01 --remote-port 3306

    # 指定本地端口
    python ssh_tunnel.py start prod-db-01 --local-port 3306 --remote-port 3306

    # 转发到远程的其他主机
    python ssh_tunnel.py start prod-web-01 --remote-host 192.168.1.100 --remote-port 8080

    # 查看所有活动的 tunnel
    python ssh_tunnel.py list

    # 停止特定 tunnel
    python ssh_tunnel.py stop prod-db-01-3306

    # 停止服务器的所有 tunnel
    python ssh_tunnel.py stop-all prod-db-01
"""

import sys
import os
import json
import socket
import threading
import time
import hashlib
import signal
import tempfile
import argparse
import traceback
from pathlib import Path
from typing import Optional, Dict, List

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))


# === 常量 ===
TUNNEL_DIR = os.path.join(tempfile.gettempdir(), 'ssh_tunnel')
IDLE_TIMEOUT = 1800  # 30 分钟空闲自动退出
HEARTBEAT_INTERVAL = 60  # 60 秒心跳检测
RECONNECT_MAX_RETRIES = 3
AUTO_PORT_START = 10000  # 自动分配端口起始值
AUTO_PORT_END = 20000    # 自动分配端口结束值


def get_tunnel_id(alias: str, local_port: int) -> str:
    """生成 tunnel 唯一标识"""
    return f"{alias}-{local_port}"


def get_tunnel_info_path(tunnel_id: str) -> str:
    """获取 tunnel 信息文件路径"""
    os.makedirs(TUNNEL_DIR, exist_ok=True)
    # 使用 MD5 避免特殊字符问题
    safe_id = hashlib.md5(tunnel_id.encode('utf-8')).hexdigest()[:16]
    return os.path.join(TUNNEL_DIR, f'{safe_id}.json')


def read_tunnel_info(tunnel_id: str) -> Optional[Dict]:
    """读取 tunnel 信息，返回 None 表示不存在或无效"""
    info_path = get_tunnel_info_path(tunnel_id)
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        # 检查进程是否存活
        pid = info.get('pid')
        if pid and _is_process_alive(pid):
            return info
        # 进程已死，清理信息文件
        os.remove(info_path)
        return None
    except Exception:
        return None


def list_all_tunnels() -> List[Dict]:
    """列出所有活动的 tunnel"""
    tunnels = []
    if not os.path.exists(TUNNEL_DIR):
        return tunnels

    for filename in os.listdir(TUNNEL_DIR):
        if not filename.endswith('.json'):
            continue
        try:
            filepath = os.path.join(TUNNEL_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                info = json.load(f)
            # 检查进程是否存活
            pid = info.get('pid')
            if pid and _is_process_alive(pid):
                tunnels.append(info)
            else:
                # 清理死进程的文件
                os.remove(filepath)
        except Exception:
            continue

    return tunnels


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def find_available_port(start: int = AUTO_PORT_START, end: int = AUTO_PORT_END) -> Optional[int]:
    """查找可用的本地端口"""
    for port in range(start, end):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.close()
            return port
        except OSError:
            continue
    return None


class SSHTunnel:
    """SSH Tunnel 守护进程"""

    def __init__(
        self,
        alias: str,
        local_port: int,
        remote_host: str,
        remote_port: int,
        idle_timeout: int = IDLE_TIMEOUT
    ):
        self.alias = alias
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.idle_timeout = idle_timeout
        self.tunnel_id = get_tunnel_id(alias, local_port)

        self._last_activity = time.time()
        self._running = False
        self._ssh_client = None
        self._server_socket = None
        self._lock = threading.Lock()
        self._connection_params = None
        self._active_connections = 0

    def start(self):
        """启动 tunnel 守护进程"""
        # 加载 SSH 配置
        self._load_config()

        # 建立 SSH 连接
        self._connect_ssh()

        # 启动本地监听
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(('127.0.0.1', self.local_port))
        self._server_socket.listen(5)
        self._server_socket.settimeout(5.0)

        self._running = True

        # 写入 tunnel 信息
        info = {
            'pid': os.getpid(),
            'tunnel_id': self.tunnel_id,
            'alias': self.alias,
            'local_port': self.local_port,
            'remote_host': self.remote_host,
            'remote_port': self.remote_port,
            'ssh_host': self._get_ssh_host_info(),
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'idle_timeout': self.idle_timeout
        }
        info_path = get_tunnel_info_path(self.tunnel_id)
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

        # 输出启动信息
        print(json.dumps({
            'success': True,
            'tunnel_id': self.tunnel_id,
            'local_port': self.local_port,
            'remote_host': self.remote_host,
            'remote_port': self.remote_port,
            'message': f'Tunnel started: 127.0.0.1:{self.local_port} -> {self.remote_host}:{self.remote_port}'
        }, ensure_ascii=False))
        sys.stdout.flush()

        # 启动心跳线程
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        # 启动空闲检测线程
        idle_thread = threading.Thread(target=self._idle_check_loop, daemon=True)
        idle_thread.start()

        # 主循环：接受连接并转发
        try:
            while self._running:
                try:
                    client_sock, addr = self._server_socket.accept()
                    self._last_activity = time.time()
                    t = threading.Thread(
                        target=self._handle_tunnel,
                        args=(client_sock,),
                        daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    if self._running:
                        raise
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _load_config(self):
        """从 SSH config 加载连接参数"""
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        self._connection_params = loader.get_connection_params(self.alias)

    def _get_ssh_host_info(self) -> str:
        """获取 SSH 服务器信息"""
        if self._connection_params:
            user = self._connection_params.get('user', 'unknown')
            host = self._connection_params.get('hostname', 'unknown')
            return f"{user}@{host}"
        return "unknown"

    def _connect_ssh(self):
        """建立 SSH 连接"""
        import paramiko

        params = self._connection_params
        if not params:
            raise ValueError("连接参数未加载")

        host = params['hostname']
        user = params['user']
        port = params['port']
        password = params.get('password')
        key_file = params.get('key_file')

        # 解析密钥路径
        if key_file:
            key_file = os.path.expanduser(key_file)

        self._ssh_client = paramiko.SSHClient()
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 处理 ProxyJump
        proxy_jump = params.get('proxyjump')
        proxy_client = None
        if proxy_jump:
            proxy_client = self._create_proxy_client(proxy_jump)

        try:
            if password:
                self._ssh_client.connect(
                    hostname=host,
                    port=port,
                    username=user,
                    password=password,
                    timeout=30,
                    sock=proxy_client.get_transport().open_channel(
                        'direct-tcpip', (host, port), ('', 0)
                    ) if proxy_client else None
                )
            elif key_file:
                self._ssh_client.connect(
                    hostname=host,
                    port=port,
                    username=user,
                    key_filename=key_file,
                    timeout=30,
                    sock=proxy_client.get_transport().open_channel(
                        'direct-tcpip', (host, port), ('', 0)
                    ) if proxy_client else None
                )
            else:
                raise ValueError("未配置认证方式（密码或密钥）")
        except Exception as e:
            if self._ssh_client:
                self._ssh_client.close()
            raise RuntimeError(f"SSH 连接失败: {e}")

    def _create_proxy_client(self, proxy_jump: str):
        """创建跳板机连接"""
        import paramiko
        from config_v3 import SSHConfigLoaderV3

        loader = SSHConfigLoaderV3()
        proxy_params = loader.get_connection_params(proxy_jump)

        proxy_client = paramiko.SSHClient()
        proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        proxy_host = proxy_params['hostname']
        proxy_port = proxy_params['port']
        proxy_user = proxy_params['user']
        proxy_password = proxy_params.get('password')
        proxy_key = proxy_params.get('key_file')

        if proxy_key:
            proxy_key = os.path.expanduser(proxy_key)

        if proxy_password:
            proxy_client.connect(
                hostname=proxy_host,
                port=proxy_port,
                username=proxy_user,
                password=proxy_password,
                timeout=30
            )
        elif proxy_key:
            proxy_client.connect(
                hostname=proxy_host,
                port=proxy_port,
                username=proxy_user,
                key_filename=proxy_key,
                timeout=30
            )
        else:
            raise ValueError(f"跳板机 {proxy_jump} 未配置认证方式")

        return proxy_client

    def _handle_tunnel(self, client_sock: socket.socket):
        """处理单个 tunnel 连接"""
        transport = None
        channel = None

        try:
            with self._lock:
                self._active_connections += 1

            # 通过 SSH 建立到远程主机的连接
            transport = self._ssh_client.get_transport()
            channel = transport.open_channel(
                'direct-tcpip',
                (self.remote_host, self.remote_port),
                ('127.0.0.1', self.local_port)
            )

            # 双向转发数据
            self._forward_data(client_sock, channel)

        except Exception as e:
            # 静默处理连接错误（客户端断开是正常情况）
            pass
        finally:
            with self._lock:
                self._active_connections -= 1
            if channel:
                channel.close()
            if client_sock:
                try:
                    client_sock.close()
                except:
                    pass

    def _forward_data(self, client_sock: socket.socket, channel):
        """双向转发数据"""
        def forward(source, destination):
            try:
                while True:
                    data = source.recv(4096)
                    if not data:
                        break
                    destination.sendall(data)
            except:
                pass

        # 启动两个线程进行双向转发
        client_to_remote = threading.Thread(
            target=forward,
            args=(client_sock, channel),
            daemon=True
        )
        remote_to_client = threading.Thread(
            target=forward,
            args=(channel, client_sock),
            daemon=True
        )

        client_to_remote.start()
        remote_to_client.start()

        # 等待任一方向断开
        client_to_remote.join()
        remote_to_client.join()

    def _heartbeat_loop(self):
        """心跳检测循环"""
        while self._running:
            time.sleep(HEARTBEAT_INTERVAL)
            if not self._running:
                break

            # 检查 SSH 连接是否存活
            try:
                if self._ssh_client and self._ssh_client.get_transport():
                    transport = self._ssh_client.get_transport()
                    if not transport.is_active():
                        # 连接断开，尝试重连
                        self._reconnect_ssh()
            except Exception:
                self._reconnect_ssh()

    def _reconnect_ssh(self):
        """重连 SSH"""
        for attempt in range(RECONNECT_MAX_RETRIES):
            try:
                if self._ssh_client:
                    try:
                        self._ssh_client.close()
                    except:
                        pass

                self._connect_ssh()
                return  # 重连成功
            except Exception:
                if attempt < RECONNECT_MAX_RETRIES - 1:
                    time.sleep(5)  # 等待后重试
                else:
                    # 重连失败，退出守护进程
                    self._running = False

    def _idle_check_loop(self):
        """空闲检测循环"""
        while self._running:
            time.sleep(60)  # 每分钟检查一次
            if not self._running:
                break

            # 检查是否超过空闲时间且无活动连接
            idle_time = time.time() - self._last_activity
            if idle_time > self.idle_timeout and self._active_connections == 0:
                self._running = False
                break

    def _shutdown(self):
        """清理资源"""
        self._running = False

        # 关闭服务器 socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass

        # 关闭 SSH 连接
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except:
                pass

        # 删除信息文件
        try:
            info_path = get_tunnel_info_path(self.tunnel_id)
            if os.path.exists(info_path):
                os.remove(info_path)
        except:
            pass


def cmd_start(args):
    """启动 tunnel"""
    alias = args.alias
    remote_port = args.remote_port
    remote_host = args.remote_host or 'localhost'
    local_port = args.local_port

    # 如果未指定本地端口，自动分配
    if not local_port:
        local_port = find_available_port()
        if not local_port:
            print(json.dumps({
                'success': False,
                'error': '无法找到可用的本地端口'
            }, ensure_ascii=False))
            return 1

    tunnel_id = get_tunnel_id(alias, local_port)

    # 检查是否已存在
    existing = read_tunnel_info(tunnel_id)
    if existing:
        print(json.dumps({
            'success': False,
            'error': f'Tunnel 已存在: {tunnel_id}',
            'tunnel_info': existing
        }, ensure_ascii=False))
        return 1

    # 检查端口是否被占用
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', local_port))
        sock.close()
    except OSError:
        print(json.dumps({
            'success': False,
            'error': f'本地端口 {local_port} 已被占用'
        }, ensure_ascii=False))
        return 1

    # 启动守护进程
    if os.name == 'nt':
        # Windows: 使用 subprocess 后台启动
        import subprocess
        subprocess.Popen(
            [sys.executable, __file__, '_daemon', alias, str(local_port), remote_host, str(remote_port)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
    else:
        # Unix: fork 后台进程
        pid = os.fork()
        if pid == 0:
            # 子进程
            os.setsid()
            sys.stdin.close()
            sys.stdout = open(os.devnull, 'w')
            sys.stderr = open(os.devnull, 'w')
            tunnel = SSHTunnel(alias, local_port, remote_host, remote_port)
            tunnel.start()
            sys.exit(0)

    # 等待守护进程启动
    time.sleep(2)

    # 验证启动成功
    info = read_tunnel_info(tunnel_id)
    if info:
        print(json.dumps({
            'success': True,
            'tunnel_id': tunnel_id,
            'local_port': local_port,
            'remote_host': remote_host,
            'remote_port': remote_port,
            'message': f'Tunnel 已启动: 127.0.0.1:{local_port} -> {remote_host}:{remote_port}'
        }, ensure_ascii=False))
        return 0
    else:
        print(json.dumps({
            'success': False,
            'error': 'Tunnel 启动失败，请检查日志'
        }, ensure_ascii=False))
        return 1


def cmd_daemon(args):
    """守护进程入口（内部使用）"""
    alias = args.alias
    local_port = int(args.local_port)
    remote_host = args.remote_host
    remote_port = int(args.remote_port)

    tunnel = SSHTunnel(alias, local_port, remote_host, remote_port)
    tunnel.start()


def cmd_list(args):
    """列出所有活动的 tunnel"""
    tunnels = list_all_tunnels()

    if not tunnels:
        print(json.dumps({
            'success': True,
            'tunnels': [],
            'count': 0,
            'message': '没有活动的 tunnel'
        }, ensure_ascii=False))
        return 0

    # 按别名和端口排序
    tunnels.sort(key=lambda t: (t.get('alias', ''), t.get('local_port', 0)))

    print(json.dumps({
        'success': True,
        'tunnels': tunnels,
        'count': len(tunnels)
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args):
    """查看 tunnel 状态"""
    tunnel_id = args.tunnel_id
    info = read_tunnel_info(tunnel_id)

    if not info:
        print(json.dumps({
            'success': False,
            'error': f'Tunnel 不存在: {tunnel_id}'
        }, ensure_ascii=False))
        return 1

    print(json.dumps({
        'success': True,
        'tunnel_info': info
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_stop(args):
    """停止 tunnel"""
    tunnel_id = args.tunnel_id
    info = read_tunnel_info(tunnel_id)

    if not info:
        print(json.dumps({
            'success': False,
            'error': f'Tunnel 不存在: {tunnel_id}'
        }, ensure_ascii=False))
        return 1

    pid = info.get('pid')
    if not pid:
        print(json.dumps({
            'success': False,
            'error': 'Tunnel 信息中缺少 PID'
        }, ensure_ascii=False))
        return 1

    # 终止进程
    try:
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
            else:
                raise OSError(f"无法打开进程 {pid}")
        else:
            os.kill(pid, signal.SIGTERM)

        # 等待进程退出
        time.sleep(1)

        # 清理信息文件
        info_path = get_tunnel_info_path(tunnel_id)
        if os.path.exists(info_path):
            os.remove(info_path)

        print(json.dumps({
            'success': True,
            'message': f'Tunnel 已停止: {tunnel_id}'
        }, ensure_ascii=False))
        return 0

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': f'停止 tunnel 失败: {e}'
        }, ensure_ascii=False))
        return 1


def cmd_stop_all(args):
    """停止指定服务器的所有 tunnel"""
    alias = args.alias
    tunnels = list_all_tunnels()

    # 过滤出指定服务器的 tunnel
    target_tunnels = [t for t in tunnels if t.get('alias') == alias]

    if not target_tunnels:
        print(json.dumps({
            'success': True,
            'message': f'没有找到服务器 {alias} 的 tunnel',
            'stopped': 0
        }, ensure_ascii=False))
        return 0

    stopped = 0
    failed = 0

    for tunnel_info in target_tunnels:
        tunnel_id = tunnel_info.get('tunnel_id')
        pid = tunnel_info.get('pid')

        try:
            if os.name == 'nt':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x0001, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)

            # 清理信息文件
            info_path = get_tunnel_info_path(tunnel_id)
            if os.path.exists(info_path):
                os.remove(info_path)

            stopped += 1
        except Exception:
            failed += 1

    print(json.dumps({
        'success': True,
        'message': f'已停止 {stopped} 个 tunnel',
        'stopped': stopped,
        'failed': failed
    }, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='SSH Tunnel 守护进程管理工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest='command', help='命令')

    # start 命令
    parser_start = subparsers.add_parser('start', help='启动 tunnel')
    parser_start.add_argument('alias', help='服务器别名')
    parser_start.add_argument('--local-port', type=int, help='本地端口（不指定则自动分配）')
    parser_start.add_argument('--remote-port', type=int, required=True, help='远程端口')
    parser_start.add_argument('--remote-host', default='localhost', help='远程主机（默认 localhost）')
    parser_start.set_defaults(func=cmd_start)

    # list 命令
    parser_list = subparsers.add_parser('list', help='列出所有活动的 tunnel')
    parser_list.set_defaults(func=cmd_list)

    # status 命令
    parser_status = subparsers.add_parser('status', help='查看 tunnel 状态')
    parser_status.add_argument('tunnel_id', help='Tunnel ID')
    parser_status.set_defaults(func=cmd_status)

    # stop 命令
    parser_stop = subparsers.add_parser('stop', help='停止 tunnel')
    parser_stop.add_argument('tunnel_id', help='Tunnel ID')
    parser_stop.set_defaults(func=cmd_stop)

    # stop-all 命令
    parser_stop_all = subparsers.add_parser('stop-all', help='停止服务器的所有 tunnel')
    parser_stop_all.add_argument('alias', help='服务器别名')
    parser_stop_all.set_defaults(func=cmd_stop_all)

    # _daemon 命令（内部使用）
    parser_daemon = subparsers.add_parser('_daemon', help=argparse.SUPPRESS)
    parser_daemon.add_argument('alias')
    parser_daemon.add_argument('local_port')
    parser_daemon.add_argument('remote_host')
    parser_daemon.add_argument('remote_port')
    parser_daemon.set_defaults(func=cmd_daemon)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

