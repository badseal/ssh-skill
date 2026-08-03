from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


TERMINAL_STATES = {"succeeded", "failed", "outcome_unknown"}


class RequestIdConflict(ValueError):
    pass


@dataclass
class RequestRecord:
    request_id: str
    fingerprint: str
    state: str
    created_at: float
    updated_at: float
    result: dict[str, Any] | None = None
    queue_ms: int | None = None
    execution_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "result": copy.deepcopy(self.result),
            "queue_ms": self.queue_ms,
            "execution_ms": self.execution_ms,
        }


class RequestRegistry:
    def __init__(
        self,
        max_entries: int = 256,
        ttl_seconds: float = 1800,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._records: OrderedDict[str, RequestRecord] = OrderedDict()
        self._lock = threading.RLock()

    def accept(self, request_id: str, fingerprint: str) -> tuple[RequestRecord, bool]:
        if not request_id or not fingerprint:
            raise ValueError("request_id and fingerprint are required")
        with self._lock:
            self._purge_locked()
            existing = self._records.get(request_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise RequestIdConflict(request_id)
                self._records.move_to_end(request_id)
                return copy.deepcopy(existing), False
            now = self._clock()
            record = RequestRecord(request_id, fingerprint, "accepted", now, now)
            self._records[request_id] = record
            self._trim_locked()
            return copy.deepcopy(record), True

    def mark_running(self, request_id: str, *, queue_ms: int) -> RequestRecord:
        with self._lock:
            record = self._require_locked(request_id)
            record.state = "running"
            record.queue_ms = max(0, queue_ms)
            record.updated_at = self._clock()
            return copy.deepcopy(record)

    def finish(
        self,
        request_id: str,
        *,
        state: str,
        result: dict[str, Any],
        execution_ms: int | None = None,
    ) -> RequestRecord:
        if state not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal state: {state}")
        with self._lock:
            record = self._require_locked(request_id)
            record.state = state
            record.result = copy.deepcopy(result)
            record.execution_ms = None if execution_ms is None else max(0, execution_ms)
            record.updated_at = self._clock()
            return copy.deepcopy(record)

    def get(self, request_id: str) -> RequestRecord | None:
        with self._lock:
            self._purge_locked()
            record = self._records.get(request_id)
            return None if record is None else copy.deepcopy(record)

    def _require_locked(self, request_id: str) -> RequestRecord:
        try:
            return self._records[request_id]
        except KeyError as exc:
            raise KeyError(f"unknown request_id: {request_id}") from exc

    def _purge_locked(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        expired = [
            request_id
            for request_id, record in self._records.items()
            if record.state in TERMINAL_STATES and record.updated_at < cutoff
        ]
        for request_id in expired:
            self._records.pop(request_id, None)

    def _trim_locked(self) -> None:
        while len(self._records) > self.max_entries:
            terminal_id = next(
                (key for key, record in self._records.items() if record.state in TERMINAL_STATES),
                None,
            )
            self._records.pop(terminal_id if terminal_id is not None else next(iter(self._records)))
