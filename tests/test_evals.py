from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "evals" / "workflows.json"
REQUIRED_CASES = {
    "windows-complex-command",
    "daemon-second-command",
    "bounded-large-transfer",
    "noninteractive-passphrase",
    "production-cluster-preview",
    "negative-local-docker",
    "negative-browser-download",
    "negative-git-remote",
    "negative-non-ssh-database",
}
AGENTS = {"codex", "claude-code"}
PLATFORMS = {"windows", "macos", "linux"}


class WorkflowEvalTests(unittest.TestCase):
    def load_cases(self):
        payload = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        self.assertEqual("ssh-skill", payload["skill_name"])
        self.assertEqual("4.0.0", payload["version"])
        return payload["cases"]

    def test_required_cases_have_strict_schema(self):
        cases = self.load_cases()
        self.assertEqual(REQUIRED_CASES, {case["id"] for case in cases})
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    {"id", "agents", "platforms", "prompt",
                     "expected_actions", "forbidden_actions"},
                    set(case),
                )
                self.assertTrue(set(case["agents"]).issubset(AGENTS))
                self.assertTrue(set(case["platforms"]).issubset(PLATFORMS))
                self.assertTrue(case["prompt"].strip())
                self.assertTrue(case["expected_actions"])
                self.assertTrue(case["forbidden_actions"])
                self.assertEqual(
                    len(case["expected_actions"]), len(set(case["expected_actions"]))
                )
                self.assertEqual(
                    len(case["forbidden_actions"]), len(set(case["forbidden_actions"]))
                )

    def test_eval_matrix_covers_all_agent_platform_pairs(self):
        coverage = {
            (agent, platform)
            for case in self.load_cases()
            for agent in case["agents"]
            for platform in case["platforms"]
        }
        self.assertEqual(
            {(agent, platform) for agent in AGENTS for platform in PLATFORMS},
            coverage,
        )

    def test_high_risk_and_negative_cases_encode_stop_rules(self):
        cases = {case["id"]: case for case in self.load_cases()}
        production = cases["production-cluster-preview"]
        for action in (
            "connect_before_preview",
            "apply_without_flag",
            "production_without_confirmation",
        ):
            self.assertIn(action, production["forbidden_actions"])

        daemon = cases["daemon-second-command"]
        self.assertIn("reuse_resolved_skill_root", daemon["expected_actions"])
        self.assertIn("rerun_doctor", daemon["forbidden_actions"])
        self.assertIn("restart_daemon", daemon["forbidden_actions"])

        for case_id in REQUIRED_CASES:
            if not case_id.startswith("negative-"):
                continue
            case = cases[case_id]
            self.assertIn("do_not_activate_ssh_skill", case["expected_actions"])
            self.assertIn("invoke_ssh_cli", case["forbidden_actions"])


if __name__ == "__main__":
    unittest.main()
