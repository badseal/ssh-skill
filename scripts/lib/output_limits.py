from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, TextIO


@dataclass(frozen=True)
class OutputLimits:
    max_bytes: int = 256 * 1024
    max_lines: int = 4000
    tail_bytes: int = 64 * 1024

    def __post_init__(self):
        if self.max_bytes < 1 or self.max_lines < 1:
            raise ValueError("output limits must be positive")
        if self.tail_bytes < 0 or self.tail_bytes > self.max_bytes:
            raise ValueError("tail_bytes must be between zero and max_bytes")


@dataclass(frozen=True)
class CollectedText:
    text: str
    truncated: bool
    total_bytes: int
    total_lines: int

    def to_meta(self) -> dict[str, Any]:
        return {
            "truncated": self.truncated,
            "total_bytes": self.total_bytes,
            "total_lines": self.total_lines,
        }


class BoundedText:
    def __init__(self, limits: OutputLimits | None = None):
        self.limits = limits or OutputLimits()
        self._head = bytearray()
        self._tail = bytearray()
        self._total_bytes = 0
        self._newline_count = 0
        self._last_byte: int | None = None

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._total_bytes += len(data)
        self._newline_count += data.count(b"\n")
        self._last_byte = data[-1]
        head_capacity = self.limits.max_bytes - self.limits.tail_bytes
        needed = max(0, head_capacity - len(self._head))
        if needed:
            self._head.extend(data[:needed])
        remainder = data[needed:]
        if self.limits.tail_bytes and remainder:
            self._tail.extend(remainder)
            if len(self._tail) > self.limits.tail_bytes:
                del self._tail[:-self.limits.tail_bytes]

    def finish(self) -> CollectedText:
        total_lines = self._newline_count + int(
            self._total_bytes > 0 and self._last_byte != ord("\n")
        )
        text = self._head.decode("utf-8", errors="ignore") + self._tail.decode(
            "utf-8", errors="ignore"
        )
        lines = text.splitlines(keepends=True)
        line_truncated = total_lines > self.limits.max_lines
        if line_truncated:
            head_count = (self.limits.max_lines + 1) // 2
            tail_count = self.limits.max_lines // 2
            text = "".join(lines[:head_count] + (lines[-tail_count:] if tail_count else []))
        byte_truncated = self._total_bytes > self.limits.max_bytes
        while len(text.encode("utf-8")) > self.limits.max_bytes:
            text = text[:-1]
        return CollectedText(
            text=text,
            truncated=byte_truncated or line_truncated,
            total_bytes=self._total_bytes,
            total_lines=total_lines,
        )


def collect_text(value: str | bytes, limits: OutputLimits | None = None) -> CollectedText:
    collector = BoundedText(limits)
    collector.feed(value.encode("utf-8") if isinstance(value, str) else value)
    return collector.finish()


def bound_result_output(
    result: dict[str, Any], limits: OutputLimits | None = None
) -> dict[str, Any]:
    bounded = dict(result)
    output_meta = dict(bounded.get("output") or {})
    for field in ("stdout", "stderr"):
        collected = collect_text(str(bounded.get(field, "")), limits)
        bounded[field] = collected.text
        output_meta[field] = collected.to_meta()
    bounded["output"] = output_meta
    return bounded


class ProgressLimiter:
    def __init__(self, min_interval_seconds: float = 1.0, min_percent_delta: float = 1.0):
        self.min_interval_seconds = min_interval_seconds
        self.min_percent_delta = min_percent_delta
        self._last_time: float | None = None
        self._last_percent = 0.0

    def should_emit(self, transferred: int, total: int, *, now: float) -> bool:
        percent = 100.0 if total <= 0 else min(100.0, transferred * 100.0 / total)
        final = total > 0 and transferred >= total
        if self._last_time is None or final:
            allowed = True
        else:
            allowed = (
                now - self._last_time >= self.min_interval_seconds
                and percent - self._last_percent >= self.min_percent_delta
            )
        if allowed:
            self._last_time = now
            self._last_percent = percent
        return allowed


def progress_is_enabled(explicit: bool | None, stream: TextIO = sys.stderr) -> bool:
    return bool(stream.isatty()) if explicit is None else explicit


class ProgressEmitter:
    def __init__(
        self,
        stream: TextIO = sys.stderr,
        *,
        enabled: bool | None = None,
        limiter: ProgressLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.stream = stream
        self.enabled = progress_is_enabled(enabled, stream)
        self.limiter = limiter or ProgressLimiter()
        self.clock = clock

    def emit(
        self,
        operation: str,
        transferred: int,
        total: int,
        **extra: Any,
    ) -> None:
        if not self.enabled or not self.limiter.should_emit(
            transferred, total, now=self.clock()
        ):
            return
        percent = 100.0 if total <= 0 else min(100.0, transferred * 100.0 / total)
        event = {
            "type": "progress",
            "operation": operation,
            "transferred_bytes": transferred,
            "total_bytes": total,
            "percent": round(percent, 2),
            **extra,
        }
        self.stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.stream.flush()
