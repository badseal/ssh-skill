"""
SFTP 高级传输模块

提供断点续传、进度回调、目录递归传输、并发传输等高级 SFTP 功能。
"""

import os
import posixpath
import stat
import time
import json
import sys
from typing import Optional, Callable, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

try:
    from .paramiko_client import SSHResult
except ImportError:
    from paramiko_client import SSHResult


CHUNK_SIZE = 128 * 1024  # 128KB 传输块（优化大文件传输性能）
MAX_RESULT_ITEMS = 100


def _bounded_items(values: list, limit: int = MAX_RESULT_ITEMS) -> list:
    if len(values) <= limit:
        return list(values)
    head_count = (limit + 1) // 2
    tail_count = limit // 2
    return list(values[:head_count]) + list(values[-tail_count:])


def _merge_bounded_items(
    current: list,
    incoming: list,
    current_total: int,
    incoming_total: int,
    limit: int = MAX_RESULT_ITEMS,
) -> list:
    if current_total + incoming_total <= limit:
        return list(current) + list(incoming)
    head_count = (limit + 1) // 2
    tail_count = limit // 2
    head = list(current[:head_count])
    if len(head) < head_count:
        head.extend(incoming[:head_count - len(head)])
    tail_source = list(current[-tail_count:]) + list(incoming[-tail_count:])
    return head + tail_source[-tail_count:]


@dataclass
class TransferProgress:
    """传输进度信息"""
    file_path: str
    total_bytes: int
    transferred_bytes: int
    start_time: float
    resumed_from: int = 0

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 100.0
        return (self.transferred_bytes / self.total_bytes) * 100

    @property
    def speed_bps(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed <= 0:
            return 0
        transferred_new = self.transferred_bytes - self.resumed_from
        return transferred_new / elapsed

    @property
    def speed_human(self) -> str:
        speed = self.speed_bps
        if speed >= 1024 * 1024:
            return f"{speed / (1024 * 1024):.1f} MB/s"
        elif speed >= 1024:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed:.0f} B/s"

    @property
    def eta_seconds(self) -> float:
        speed = self.speed_bps
        if speed <= 0:
            return -1
        remaining = self.total_bytes - self.transferred_bytes
        return remaining / speed

    def to_dict(self) -> dict:
        return {
            'file': os.path.basename(self.file_path),
            'total': self.total_bytes,
            'transferred': self.transferred_bytes,
            'percent': round(self.percent, 1),
            'speed': self.speed_human,
            'eta': round(self.eta_seconds, 1) if self.eta_seconds >= 0 else None,
        }


@dataclass
class TransferResult:
    """传输结果"""
    success: bool
    files_transferred: int = 0
    files_failed: int = 0
    bytes_transferred: int = 0
    errors: List[str] = field(default_factory=list)
    details: List[Dict] = field(default_factory=list)
    errors_total: int = 0
    details_total: int = 0

    def __post_init__(self):
        self.errors_total = max(self.errors_total, len(self.errors))
        self.details_total = max(self.details_total, len(self.details))
        self.errors = _bounded_items(self.errors)
        self.details = _bounded_items(self.details)

    def merge(self, other: "TransferResult") -> None:
        self.errors = _merge_bounded_items(
            self.errors,
            other.errors,
            self.errors_total,
            other.errors_total,
        )
        self.details = _merge_bounded_items(
            self.details,
            other.details,
            self.details_total,
            other.details_total,
        )
        self.errors_total += other.errors_total
        self.details_total += other.details_total
        self.bytes_transferred += other.bytes_transferred
        self.files_transferred += other.files_transferred
        self.files_failed += other.files_failed
        self.success = self.success and other.success

    def add_error(self, message: str) -> None:
        self.errors = _merge_bounded_items(
            self.errors, [message], self.errors_total, 1
        )
        self.errors_total += 1

    def to_dict(self) -> dict:
        return {
            'success': self.success,
            'files_transferred': self.files_transferred,
            'files_failed': self.files_failed,
            'bytes_transferred': self.bytes_transferred,
            'bytes_human': _human_size(self.bytes_transferred),
            'errors': list(self.errors),
            'details': list(self.details),
            'errors_total': self.errors_total,
            'details_total': self.details_total,
            'errors_truncated': len(self.errors) < self.errors_total,
            'details_truncated': len(self.details) < self.details_total,
        }


def _human_size(size_bytes: int) -> str:
    """将字节数转为人类可读格式"""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"


def _remote_exists(sftp, path: str) -> bool:
    """检查远程路径是否存在"""
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False
    except IOError:
        return False


def _remote_isdir(sftp, path: str) -> bool:
    """检查远程路径是否为目录"""
    try:
        return stat.S_ISDIR(sftp.stat(path).st_mode)
    except Exception:
        return False


def _remote_mkdir_p(sftp, remote_dir: str):
    """递归创建远程目录（类似 mkdir -p）"""
    dirs_to_create = []
    current = remote_dir

    while current and current != '/':
        if _remote_exists(sftp, current):
            break
        dirs_to_create.insert(0, current)
        parent = posixpath.dirname(current)
        if parent == current:
            break  # 防止无限循环
        current = parent

    for d in dirs_to_create:
        try:
            sftp.mkdir(d)
        except IOError:
            pass  # 目录可能已存在（并发场景）


class SFTPTransfer:
    """SFTP 高级传输"""

    def __init__(self, sftp, progress_callback: Optional[Callable] = None):
        """
        Args:
            sftp: paramiko SFTPClient 实例
            progress_callback: 进度回调函数，签名 callback(TransferProgress)
        """
        self.sftp = sftp
        self.progress_callback = progress_callback

    def upload_file(self, local_path: str, remote_path: str,
                    resume: bool = False) -> TransferResult:
        """
        上传单个文件，支持断点续传

        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径（如果是目录则自动追加文件名）
            resume: 是否断点续传
        """
        local_path = os.path.abspath(local_path)
        if not os.path.isfile(local_path):
            return TransferResult(
                success=False, errors=[f"本地文件不存在: {local_path}"]
            )

        local_size = os.path.getsize(local_path)

        # 如果远程路径是目录，追加文件名
        if _remote_isdir(self.sftp, remote_path):
            remote_path = remote_path.rstrip('/') + '/' + os.path.basename(local_path)

        # 确保远程目录存在
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            _remote_mkdir_p(self.sftp, remote_dir)

        # 断点续传：获取远程已有大小
        resume_offset = 0
        if resume:
            try:
                remote_stat = self.sftp.stat(remote_path)
                resume_offset = remote_stat.st_size
                if resume_offset >= local_size:
                    # 文件已完整上传
                    return TransferResult(
                        success=True,
                        files_transferred=1,
                        bytes_transferred=local_size,
                        details=[{
                            'file': os.path.basename(local_path),
                            'status': 'already_complete',
                            'size': local_size,
                        }]
                    )
            except (FileNotFoundError, IOError):
                resume_offset = 0

        # 创建进度跟踪
        progress = TransferProgress(
            file_path=local_path,
            total_bytes=local_size,
            transferred_bytes=resume_offset,
            start_time=time.time(),
            resumed_from=resume_offset,
        )

        try:
            if resume_offset > 0:
                # 断点续传：追加模式
                with open(local_path, 'rb') as local_f:
                    local_f.seek(resume_offset)
                    with self.sftp.open(remote_path, 'ab') as remote_f:
                        while True:
                            chunk = local_f.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            remote_f.write(chunk)
                            progress.transferred_bytes += len(chunk)
                            if self.progress_callback:
                                self.progress_callback(progress)
            else:
                # 全新上传：使用分块写入以支持进度回调
                with open(local_path, 'rb') as local_f:
                    with self.sftp.open(remote_path, 'wb') as remote_f:
                        # 设置预取缓冲区大小以提升性能
                        remote_f.set_pipelined(True)
                        while True:
                            chunk = local_f.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            remote_f.write(chunk)
                            progress.transferred_bytes += len(chunk)
                            if self.progress_callback:
                                self.progress_callback(progress)

            return TransferResult(
                success=True,
                files_transferred=1,
                bytes_transferred=local_size,
                details=[{
                    'file': os.path.basename(local_path),
                    'status': 'uploaded',
                    'size': local_size,
                    'resumed_from': resume_offset if resume_offset > 0 else None,
                    'speed': progress.speed_human,
                }]
            )
        except Exception as e:
            return TransferResult(
                success=False,
                files_failed=1,
                bytes_transferred=progress.transferred_bytes,
                errors=[f"上传失败 {os.path.basename(local_path)}: {e}"],
            )

    def download_file(self, remote_path: str, local_path: str,
                      resume: bool = False) -> TransferResult:
        """
        下载单个文件，支持断点续传

        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径（如果是目录则自动追加文件名）
            resume: 是否断点续传
        """
        # 获取远程文件信息
        try:
            remote_stat = self.sftp.stat(remote_path)
            remote_size = remote_stat.st_size
        except (FileNotFoundError, IOError) as e:
            return TransferResult(
                success=False, errors=[f"远程文件不存在: {remote_path}"]
            )

        # 如果本地路径是目录，追加文件名
        local_path = os.path.abspath(local_path)
        if os.path.isdir(local_path):
            local_path = os.path.join(local_path, posixpath.basename(remote_path))

        # 确保本地目录存在
        local_dir = os.path.dirname(local_path)
        if local_dir and not os.path.exists(local_dir):
            os.makedirs(local_dir, exist_ok=True)

        # 断点续传
        resume_offset = 0
        if resume and os.path.exists(local_path):
            resume_offset = os.path.getsize(local_path)
            if resume_offset >= remote_size:
                return TransferResult(
                    success=True,
                    files_transferred=1,
                    bytes_transferred=remote_size,
                    details=[{
                        'file': posixpath.basename(remote_path),
                        'status': 'already_complete',
                        'size': remote_size,
                    }]
                )

        progress = TransferProgress(
            file_path=remote_path,
            total_bytes=remote_size,
            transferred_bytes=resume_offset,
            start_time=time.time(),
            resumed_from=resume_offset,
        )

        try:
            if resume_offset > 0:
                # 断点续传
                with self.sftp.open(remote_path, 'rb') as remote_f:
                    remote_f.seek(resume_offset)
                    with open(local_path, 'ab') as local_f:
                        while True:
                            chunk = remote_f.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            local_f.write(chunk)
                            progress.transferred_bytes += len(chunk)
                            if self.progress_callback:
                                self.progress_callback(progress)
            else:
                # 全新下载
                with self.sftp.open(remote_path, 'rb') as remote_f:
                    with open(local_path, 'wb') as local_f:
                        while True:
                            chunk = remote_f.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            local_f.write(chunk)
                            progress.transferred_bytes += len(chunk)
                            if self.progress_callback:
                                self.progress_callback(progress)

            return TransferResult(
                success=True,
                files_transferred=1,
                bytes_transferred=remote_size,
                details=[{
                    'file': posixpath.basename(remote_path),
                    'status': 'downloaded',
                    'size': remote_size,
                    'resumed_from': resume_offset if resume_offset > 0 else None,
                    'speed': progress.speed_human,
                }]
            )
        except Exception as e:
            return TransferResult(
                success=False,
                files_failed=1,
                bytes_transferred=progress.transferred_bytes,
                errors=[f"下载失败 {posixpath.basename(remote_path)}: {e}"],
            )

    def upload_directory(self, local_dir: str, remote_dir: str,
                         resume: bool = False) -> TransferResult:
        """
        递归上传目录

        Args:
            local_dir: 本地目录路径
            remote_dir: 远程目标目录
            resume: 是否断点续传
        """
        local_dir = os.path.abspath(local_dir)
        if not os.path.isdir(local_dir):
            return TransferResult(
                success=False, errors=[f"本地目录不存在: {local_dir}"]
            )

        # 确保远程根目录存在
        _remote_mkdir_p(self.sftp, remote_dir)

        result = TransferResult(success=True)

        for root, dirs, files in os.walk(local_dir):
            # 计算相对路径
            rel_path = os.path.relpath(root, local_dir)
            if rel_path == '.':
                current_remote = remote_dir
            else:
                current_remote = remote_dir.rstrip('/') + '/' + rel_path.replace('\\', '/')

            # 创建远程子目录
            _remote_mkdir_p(self.sftp, current_remote)

            # 上传文件
            for filename in files:
                local_file = os.path.join(root, filename)
                remote_file = current_remote.rstrip('/') + '/' + filename

                file_result = self.upload_file(local_file, remote_file, resume=resume)

                result.merge(file_result)

        if result.files_failed > 0:
            result.success = False

        return result

    def download_directory(self, remote_dir: str, local_dir: str,
                           resume: bool = False) -> TransferResult:
        """
        递归下载目录

        Args:
            remote_dir: 远程目录路径
            local_dir: 本地目标目录
            resume: 是否断点续传
        """
        if not _remote_isdir(self.sftp, remote_dir):
            return TransferResult(
                success=False, errors=[f"远程目录不存在: {remote_dir}"]
            )

        local_dir = os.path.abspath(local_dir)
        os.makedirs(local_dir, exist_ok=True)

        result = TransferResult(success=True)
        self._download_dir_recursive(remote_dir, local_dir, resume, result)

        if result.files_failed > 0:
            result.success = False

        return result

    def _download_dir_recursive(self, remote_dir: str, local_dir: str,
                                resume: bool, result: TransferResult):
        """递归下载目录内容"""
        try:
            entries = self.sftp.listdir_attr(remote_dir)
        except Exception as e:
            result.add_error(f"无法列出目录 {remote_dir}: {e}")
            result.files_failed += 1
            return

        for entry in entries:
            remote_path = remote_dir.rstrip('/') + '/' + entry.filename
            local_path = os.path.join(local_dir, entry.filename)

            if stat.S_ISDIR(entry.st_mode):
                os.makedirs(local_path, exist_ok=True)
                self._download_dir_recursive(remote_path, local_path, resume, result)
            else:
                file_result = self.download_file(remote_path, local_path, resume=resume)
                result.merge(file_result)


def parallel_upload(sftp_factory: Callable, file_list: List[Tuple[str, str]],
                    resume: bool = False, max_workers: int = 4,
                    progress_callback: Optional[Callable] = None) -> TransferResult:
    """
    并发上传多个文件

    Args:
        sftp_factory: 创建 SFTP 客户端的工厂函数（每个线程需要独立连接）
        file_list: [(local_path, remote_path), ...] 文件对列表
        resume: 是否断点续传
        max_workers: 最大并发数
        progress_callback: 进度回调
    """
    result = TransferResult(success=True)

    def upload_one(local_path, remote_path):
        sftp = sftp_factory()
        try:
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback)
            return transfer.upload_file(local_path, remote_path, resume=resume)
        finally:
            try:
                sftp.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(upload_one, lp, rp): (lp, rp)
            for lp, rp in file_list
        }

        for future in as_completed(futures):
            file_result = future.result()
            result.merge(file_result)

    if result.files_failed > 0:
        result.success = False

    return result


def parallel_download(sftp_factory: Callable, file_list: List[Tuple[str, str]],
                      resume: bool = False, max_workers: int = 4,
                      progress_callback: Optional[Callable] = None) -> TransferResult:
    """
    并发下载多个文件

    Args:
        sftp_factory: 创建 SFTP 客户端的工厂函数
        file_list: [(remote_path, local_path), ...] 文件对列表
        resume: 是否断点续传
        max_workers: 最大并发数
        progress_callback: 进度回调
    """
    result = TransferResult(success=True)

    def download_one(remote_path, local_path):
        sftp = sftp_factory()
        try:
            transfer = SFTPTransfer(sftp, progress_callback=progress_callback)
            return transfer.download_file(remote_path, local_path, resume=resume)
        finally:
            try:
                sftp.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_one, rp, lp): (rp, lp)
            for rp, lp in file_list
        }

        for future in as_completed(futures):
            file_result = future.result()
            result.merge(file_result)

    if result.files_failed > 0:
        result.success = False

    return result
