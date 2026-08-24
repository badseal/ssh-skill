from __future__ import annotations

import unittest

from tests import support  # noqa: F401
from request_registry import RequestIdConflict, RequestRegistry


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class RequestRegistryTests(unittest.TestCase):
    def test_duplicate_id_returns_cached_result(self):
        registry = RequestRegistry(max_entries=4, ttl_seconds=60, clock=FakeClock())
        _, created = registry.accept("req-1", "sha256:a")
        self.assertTrue(created)
        registry.finish("req-1", state="succeeded", result={"exit_code": 0}, execution_ms=7)

        record, created = registry.accept("req-1", "sha256:a")

        self.assertFalse(created)
        self.assertEqual({"exit_code": 0}, record.result)
        self.assertEqual("succeeded", record.state)

    def test_same_id_with_different_fingerprint_is_rejected(self):
        registry = RequestRegistry()
        registry.accept("req-1", "sha256:a")
        with self.assertRaises(RequestIdConflict):
            registry.accept("req-1", "sha256:b")

    def test_queue_and_execution_times_are_recorded_separately(self):
        registry = RequestRegistry()
        registry.accept("req-1", "sha256:a")
        registry.mark_running("req-1", queue_ms=900)
        registry.finish("req-1", state="failed", result={"exit_code": 1}, execution_ms=25)

        record = registry.get("req-1")

        self.assertEqual(900, record.queue_ms)
        self.assertEqual(25, record.execution_ms)

    def test_terminal_records_expire_after_ttl(self):
        clock = FakeClock()
        registry = RequestRegistry(ttl_seconds=5, clock=clock)
        registry.accept("req-1", "sha256:a")
        registry.finish("req-1", state="succeeded", result={})
        clock.advance(6)

        self.assertIsNone(registry.get("req-1"))


if __name__ == "__main__":
    unittest.main()
