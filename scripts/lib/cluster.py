"""
批量服务器操作模块 v3.1

从 SSH config 读取服务器列表，提供多服务器并发操作能力。
支持智能客户端选择（密钥认证用原生 SSH，密码认证用 Paramiko）。
"""

from typing import List, Dict, Optional, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .cluster_plan import ClusterPlan, resolve_cluster_plan
    from .config_v3 import SSHConfigLoaderV3
    from .native_ssh_client import SSHResult
except ImportError:
    from cluster_plan import ClusterPlan, resolve_cluster_plan
    from config_v3 import SSHConfigLoaderV3
    from native_ssh_client import SSHResult


class SSHCluster:
    """SSH集群管理类 v3.1，从 SSH config 读取服务器列表，智能选择客户端类型"""

    def __init__(self, clients: Dict[str, object], max_workers: int = 10,
                 client_errors: Dict[str, str] | None = None):
        """
        初始化集群

        Args:
            clients: {alias: NativeSSHClient 或 ParamikoClient}
            max_workers: 最大并发数
        """
        self.clients = clients
        self.max_workers = max_workers
        self.client_errors = client_errors or {}

    @classmethod
    def from_ssh_config(cls, aliases: List[str] = None,
                        environment: str = None,
                        tags: List[str] = None,
                        max_workers: int = 10) -> 'SSHCluster':
        """
        从 SSH config 创建集群（智能选择客户端类型）

        Args:
            aliases: 指定别名列表（可选）
            environment: 按环境过滤（可选）
            tags: 按标签过滤（可选）
            max_workers: 最大并发数
        """
        loader = SSHConfigLoaderV3()

        plan = resolve_cluster_plan(loader, aliases, environment, tags)
        return cls.from_plan(plan, loader=loader, max_workers=max_workers)

    @classmethod
    def from_plan(
        cls,
        plan: ClusterPlan,
        *,
        loader: SSHConfigLoaderV3 | None = None,
        max_workers: int = 10,
    ) -> 'SSHCluster':
        """仅为已经过确认的计划创建客户端。"""
        loader = loader or SSHConfigLoaderV3()
        clients = {}
        client_errors = {}
        for alias in plan.targets:
            try:
                client = loader.from_alias(alias)
                clients[alias] = client
            except Exception as exc:
                client_errors[alias] = str(exc)

        return cls(clients, max_workers, client_errors)

    @staticmethod
    def _list_all_hosts(loader: SSHConfigLoaderV3) -> List[str]:
        """从 SSH config 列出所有 Host 别名"""
        return loader.list_hosts()

    def execute_all(self, command: str, parallel: bool = True,
                    timeout: Optional[int] = None) -> Dict[str, SSHResult]:
        """在所有服务器上执行命令"""
        if parallel:
            return self._execute_parallel(command, timeout)
        else:
            return self._execute_serial(command, timeout)

    def _execute_serial(self, command: str, timeout: Optional[int]) -> Dict[str, SSHResult]:
        """串行执行命令"""
        results = {}
        for alias, client in self.clients.items():
            try:
                if timeout:
                    original_timeout = client.timeout
                    client.timeout = timeout
                    result = client.execute(command)
                    client.timeout = original_timeout
                else:
                    result = client.execute(command)
                results[alias] = result
            except Exception as e:
                results[alias] = SSHResult(
                    success=False, stdout="",
                    stderr=f"执行异常: {str(e)}", exit_code=-1
                )
        return results

    def _execute_parallel(self, command: str, timeout: Optional[int]) -> Dict[str, SSHResult]:
        """并发执行命令"""
        results = {}

        def execute_on_client(alias, client):
            try:
                if timeout:
                    original_timeout = client.timeout
                    client.timeout = timeout
                    result = client.execute(command)
                    client.timeout = original_timeout
                else:
                    result = client.execute(command)
                return alias, result
            except Exception as e:
                return alias, SSHResult(
                    success=False, stdout="",
                    stderr=f"执行异常: {str(e)}", exit_code=-1
                )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(execute_on_client, alias, client): alias
                for alias, client in self.clients.items()
            }
            for future in as_completed(futures):
                alias, result = future.result()
                results[alias] = result

        return results

    def health_check_all(self, check_command: str = "echo 'OK'",
                         parallel: bool = True,
                         timeout: Optional[int] = None) -> Dict[str, bool]:
        """批量健康检查"""
        results = self.execute_all(check_command, parallel=parallel, timeout=timeout)
        return {name: result.success for name, result in results.items()}

    def upload_all(self, local_path: str, remote_path: str,
                   parallel: bool = True) -> Dict[str, SSHResult]:
        """批量上传文件"""
        def upload_to_client(alias, client):
            try:
                result = client.upload(local_path, remote_path)
                return alias, result
            except Exception as e:
                return alias, SSHResult(
                    success=False, stdout="",
                    stderr=f"上传异常: {str(e)}", exit_code=-1
                )

        results = {}
        if parallel:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(upload_to_client, alias, client): alias
                    for alias, client in self.clients.items()
                }
                for future in as_completed(futures):
                    alias, result = future.result()
                    results[alias] = result
        else:
            for alias, client in self.clients.items():
                _, result = upload_to_client(alias, client)
                results[alias] = result

        return results
