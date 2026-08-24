from __future__ import annotations

import io
import json
import unittest

from tests import support  # noqa: F401
from result_protocol import (
    MAX_RESULT_BYTES,
    error_result,
    exit_code_for,
    success_result,
    write_result,
)


class ResultProtocolTests(unittest.TestCase):
    def test_success_envelope_has_stable_shape(self):
        result = success_result(
            "exec",
            {"stdout": "ok"},
            request_id="req-1",
            platform="windows",
            transport="openssh",
            elapsed_ms=12,
        )

        self.assertEqual("1.0", result["schema_version"])
        self.assertTrue(result["success"])
        self.assertEqual("exec", result["operation"])
        self.assertIsNone(result["error"])
        self.assertEqual("req-1", result["meta"]["request_id"])
        self.assertEqual([], result["meta"]["warnings"])

    def test_failure_has_structured_error_and_unknown_exit_code(self):
        result = error_result(
            "exec",
            code="outcome_unknown",
            message="status unavailable",
            retryable=False,
            outcome="unknown",
            request_id="req-2",
        )

        self.assertEqual(
            {
                "code": "outcome_unknown",
                "message": "status unavailable",
                "retryable": False,
                "outcome": "unknown",
            },
            result["error"],
        )
        self.assertEqual(2, exit_code_for(result))

    def test_write_result_emits_exactly_one_compact_json_document(self):
        stream = io.StringIO()
        result = success_result("doctor", {"ready": True})

        write_result(result, stream=stream)

        self.assertEqual(1, len(stream.getvalue().splitlines()))
        self.assertEqual(result, json.loads(stream.getvalue()))

    def test_write_result_is_ascii_safe_and_round_trips_unicode(self):
        stream = io.StringIO()
        result = success_result(
            "doctor", {"path": "D:/Lab/Systems/AI-Skill-\u5f00\u53d1"}
        )

        write_result(result, stream=stream)

        self.assertTrue(stream.getvalue().isascii())
        self.assertEqual(result, json.loads(stream.getvalue()))

    def test_write_result_bounds_large_nested_envelope(self):
        stream = io.StringIO()
        result = success_result(
            "cluster",
            {
                "results": {
                    f"host-{index:04d}": {"stdout": "x" * 4096, "stderr": ""}
                    for index in range(200)
                }
            },
        )

        write_result(result, stream=stream)

        encoded = stream.getvalue().encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_RESULT_BYTES + 1)
        parsed = json.loads(stream.getvalue())
        self.assertTrue(parsed["meta"]["output"]["truncated"])
        self.assertGreater(parsed["meta"]["output"]["total_bytes"], MAX_RESULT_BYTES)

    def test_invalid_outcome_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outcome"):
            error_result(
                "exec",
                code="bad",
                message="bad",
                outcome="maybe",
            )


if __name__ == "__main__":
    unittest.main()
