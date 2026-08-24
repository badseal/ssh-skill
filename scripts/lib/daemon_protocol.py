from __future__ import annotations

import json
import hashlib
import socket
import struct
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = "1.0"
MAX_FRAME_BYTES = 2 * 1024 * 1024


class FrameTooLarge(ValueError):
    pass


class FrameSendError(ConnectionError):
    def __init__(self, message: str, *, bytes_sent: int):
        super().__init__(message)
        self.bytes_sent = bytes_sent


@dataclass(frozen=True)
class SendFailureDisposition:
    disposition: str
    request_id: str


def classify_send_failure(error: FrameSendError, request_id: str) -> SendFailureDisposition:
    disposition = "not_sent" if error.bytes_sent == 0 else "outcome_unknown"
    return SendFailureDisposition(disposition, request_id)


def fingerprint_execution(command: str, remote_timeout: int) -> str:
    canonical = json.dumps(
        {"command": command, "remote_timeout": remote_timeout},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def encode_frame(message: dict[str, Any], max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > max_bytes:
        raise FrameTooLarge(f"frame exceeds {max_bytes} bytes: {len(payload)}")
    return struct.pack("!I", len(payload)) + payload


def send_frame(sock: socket.socket, message: dict[str, Any], max_bytes: int = MAX_FRAME_BYTES) -> int:
    frame = encode_frame(message, max_bytes=max_bytes)
    sent = 0
    try:
        while sent < len(frame):
            count = sock.send(frame[sent:])
            if not count:
                raise ConnectionResetError("socket closed while sending frame")
            sent += count
    except FrameSendError:
        raise
    except Exception as exc:
        raise FrameSendError(str(exc), bytes_sent=sent) from exc
    return sent


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(min(65536, length - len(chunks)))
        if not chunk:
            raise ConnectionError("socket closed while receiving frame")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_frame(
    sock: socket.socket,
    timeout: float | None = None,
    max_bytes: int = MAX_FRAME_BYTES,
) -> dict[str, Any]:
    if timeout is not None:
        sock.settimeout(timeout)
    length = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if length > max_bytes:
        raise FrameTooLarge(f"frame exceeds {max_bytes} bytes: {length}")
    value = json.loads(_recv_exact(sock, length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("daemon frame must contain a JSON object")
    return value
