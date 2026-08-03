from __future__ import annotations

import io
import json
import unittest

from tests import support  # noqa: F401
from cluster_plan import (
    ConfirmationRequired,
    resolve_cluster_plan,
    validate_cluster_apply,
)
from ssh_cluster import main


class FakeLoader:
    def __init__(self, records):
        self.records = dict(records)
        self.client_creations = 0

    def list_hosts(self):
        return list(self.records)

    def load_metadata(self, alias):
        return dict(self.records[alias])

    def from_alias(self, alias):
        self.client_creations += 1
        return object()


class ClusterPlanTests(unittest.TestCase):
    def setUp(self):
        self.loader = FakeLoader(
            [
                ("prod-b", {"environment": "production", "tags": ["web"]}),
                ("dev-a", {"environment": "development", "tags": ["web"]}),
                ("prod-a", {"environment": "production", "tags": ["web"]}),
            ]
        )

    def test_preview_resolves_targets_without_creating_clients(self):
        plan = resolve_cluster_plan(
            self.loader, None, "production", ["web"]
        )

        self.assertEqual(["prod-a", "prod-b"], plan.targets)
        self.assertEqual(["prod-a", "prod-b"], plan.production_targets)
        self.assertEqual(0, self.loader.client_creations)

    def test_apply_is_required_before_any_cluster_connection(self):
        plan = resolve_cluster_plan(self.loader, ["prod-a"], None, None)

        with self.assertRaisesRegex(ConfirmationRequired, "--apply"):
            validate_cluster_apply(plan, False, False)
        self.assertEqual(0, self.loader.client_creations)

    def test_production_requires_second_confirmation(self):
        plan = resolve_cluster_plan(self.loader, ["prod-a"], None, None)

        with self.assertRaisesRegex(
            ConfirmationRequired, "--confirm-production"
        ):
            validate_cluster_apply(plan, True, False)
        self.assertEqual(0, self.loader.client_creations)

    def test_production_markers_are_case_insensitive_and_include_prod(self):
        loader = FakeLoader([
            ("tagged", {"environment": "unknown", "tags": ["Production"]}),
            ("short-env", {"environment": "prod", "tags": []}),
        ])

        plan = resolve_cluster_plan(loader, None, None, None)

        self.assertEqual(["short-env", "tagged"], plan.production_targets)

    def test_cli_preview_emits_plan_without_creating_clients(self):
        stdout = io.StringIO()

        code = main(
            ["uptime", "--environment", "production"],
            loader=self.loader,
            stdout=stdout,
        )

        result = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("preview", result["data"]["mode"])
        self.assertEqual(["prod-a", "prod-b"], result["data"]["targets"])
        self.assertEqual(0, self.loader.client_creations)

    def test_cli_production_confirmation_failure_creates_no_clients(self):
        stdout = io.StringIO()

        code = main(
            ["uptime", "--hosts", "prod-a", "--apply"],
            loader=self.loader,
            stdout=stdout,
        )

        result = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("confirmation_required", result["error"]["code"])
        self.assertEqual(0, self.loader.client_creations)

    def test_client_creation_failure_is_not_silently_skipped(self):
        class FailingLoader(FakeLoader):
            def from_alias(self, alias):
                self.client_creations += 1
                raise ValueError(f"invalid config: {alias}")

        loader = FailingLoader([
            ("dev-a", {"environment": "development", "tags": []}),
        ])
        stdout = io.StringIO()

        code = main(
            ["uptime", "--hosts", "dev-a", "--apply"],
            loader=loader,
            stdout=stdout,
        )

        result = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("client_creation_failed", result["error"]["code"])
        self.assertIn("dev-a", result["data"]["client_errors"])


if __name__ == "__main__":
    unittest.main()
