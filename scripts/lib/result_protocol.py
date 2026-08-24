from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from output_limits import OutputLimits, collect_text


SCHEMA_VERSION = "1.0"
VALID_OUTCOMES = {"failed", "unknown"}
MAX_RESULT_BYTES = 256 * 1024
_BOUND_STEPS = (
    (100, 16 * 1024),
    (50, 8 * 1024),
    (20, 4 * 1024),
    (10, 2 * 1024),
    (5, 1024),
    (1, 512),
)


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


def _encoded_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=True, separators=(",", ":"))


def _bounded_collection(values, limit: int):
    if len(values) <= limit:
        return list(values)
    head_count = (limit + 1) // 2
    tail_count = limit // 2
    tail = values[-tail_count:] if tail_count else []
    return list(values[:head_count]) + list(tail)


def _bound_nested(
    value: Any,
    *,
    max_items: int,
    max_string_bytes: int,
    path: str,
    truncated_paths: set[str],
) -> Any:
    if isinstance(value, str):
        collected = collect_text(
            value,
            OutputLimits(
                max_bytes=max_string_bytes,
                max_lines=4000,
                tail_bytes=max_string_bytes // 4,
            ),
        )
        if collected.truncated:
            truncated_paths.add(path)
        return collected.text
    if isinstance(value, dict):
        items = list(value.items())
        bounded_items = _bounded_collection(items, max_items)
        if len(bounded_items) < len(items):
            truncated_paths.add(path)
        return {
            key: _bound_nested(
                item,
                max_items=max_items,
                max_string_bytes=max_string_bytes,
                path=f"{path}.{key}",
                truncated_paths=truncated_paths,
            )
            for key, item in bounded_items
        }
    if isinstance(value, (list, tuple)):
        bounded_items = _bounded_collection(value, max_items)
        if len(bounded_items) < len(value):
            truncated_paths.add(path)
        return [
            _bound_nested(
                item,
                max_items=max_items,
                max_string_bytes=max_string_bytes,
                path=f"{path}[{index}]",
                truncated_paths=truncated_paths,
            )
            for index, item in enumerate(bounded_items)
        ]
    return value


def _with_output_meta(
    result: dict[str, Any], total_bytes: int, truncated_paths: set[str]
) -> dict[str, Any]:
    bounded = dict(result)
    meta = dict(bounded.get("meta") or {})
    meta["output"] = {
        "truncated": True,
        "total_bytes": total_bytes,
        "max_bytes": MAX_RESULT_BYTES,
        "truncated_paths": sorted(truncated_paths)[:100],
    }
    bounded["meta"] = meta
    return bounded


def bound_result_envelope(result: dict[str, Any]) -> dict[str, Any]:
    original = _encoded_result(result)
    total_bytes = len(original.encode("utf-8"))
    if total_bytes <= MAX_RESULT_BYTES:
        return result

    for max_items, max_string_bytes in _BOUND_STEPS:
        truncated_paths: set[str] = set()
        candidate = {
            key: _bound_nested(
                value,
                max_items=max_items,
                max_string_bytes=max_string_bytes,
                path=f"$.{key}",
                truncated_paths=truncated_paths,
            )
            for key, value in result.items()
        }
        candidate = _with_output_meta(candidate, total_bytes, truncated_paths)
        if len(_encoded_result(candidate).encode("utf-8")) <= MAX_RESULT_BYTES:
            return candidate

    error = result.get("error")
    fallback_error = None
    if isinstance(error, dict):
        fallback_error = {
            "code": error.get("code"),
            "message": collect_text(
                str(error.get("message", "")),
                OutputLimits(max_bytes=512, max_lines=20, tail_bytes=128),
            ).text,
            "retryable": bool(error.get("retryable", False)),
            "outcome": error.get("outcome", "failed"),
        }
    fallback = {
        "schema_version": result.get("schema_version", SCHEMA_VERSION),
        "success": bool(result.get("success")),
        "operation": result.get("operation", "unknown"),
        "data": {},
        "error": fallback_error,
        "meta": {},
    }
    return _with_output_meta(fallback, total_bytes, {"$.data"})


def write_result(result: dict[str, Any], stream: TextIO = sys.stdout) -> None:
    stream.write(_encoded_result(bound_result_envelope(result)))
    stream.write("\n")


emit_result = write_result


def exit_code_for(result: dict[str, Any]) -> int:
    if result.get("success"):
        return 0
    error = result.get("error") or {}
    return 2 if error.get("outcome") == "unknown" else 1
