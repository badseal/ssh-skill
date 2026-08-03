from __future__ import annotations

import json
import sys
from typing import Any, TextIO


SCHEMA_VERSION = "1.0"
VALID_OUTCOMES = {"failed", "unknown"}


def _meta(
    request_id: str | None,
    platform: str | None,
    transport: str | None,
    elapsed_ms: int | None,
    warnings: list[str] | None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "platform": platform,
        "transport": transport,
        "elapsed_ms": elapsed_ms,
        "warnings": list(warnings or []),
    }


def success_result(
    operation: str,
    data: dict[str, Any],
    *,
    request_id: str | None = None,
    platform: str | None = None,
    transport: str | None = None,
    elapsed_ms: int | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "success": True,
        "operation": operation,
        "data": data,
        "error": None,
        "meta": _meta(request_id, platform, transport, elapsed_ms, warnings),
    }


def error_result(
    operation: str,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    outcome: str = "failed",
    request_id: str | None = None,
    data: dict[str, Any] | None = None,
    platform: str | None = None,
    transport: str | None = None,
    elapsed_ms: int | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    return {
        "schema_version": SCHEMA_VERSION,
        "success": False,
        "operation": operation,
        "data": {} if data is None else data,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "outcome": outcome,
        },
        "meta": _meta(request_id, platform, transport, elapsed_ms, warnings),
    }


def write_result(result: dict[str, Any], stream: TextIO = sys.stdout) -> None:
    stream.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    stream.write("\n")


emit_result = write_result


def exit_code_for(result: dict[str, Any]) -> int:
    if result.get("success"):
        return 0
    error = result.get("error") or {}
    return 2 if error.get("outcome") == "unknown" else 1
