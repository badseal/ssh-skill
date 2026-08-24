from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401
from doctor import (
    DoctorContext,
    discover_skill_copies,
    hash_skill_surface,
    run_doctor,
)
from platform_adapter import AgentStatus
from ssh_skill import main


def create_skill(root: Path, version: str, marker: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: ssh-skill\nversion: {version}\n---\n# {marker}\n",
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "marker.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    return root


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_current_root_is_first_and_duplicate_paths_are_collapsed(self):
        current = create_skill(self.root / "source", "4.0.0", "current")
        explicit = create_skill(self.root / "explicit", "4.0.0", "explicit")
        home = self.root / "home"
        project = self.root / "project"
        codex_home = home / ".codex"
        create_skill(home / ".agents" / "skills" / "ssh-skill", "3.3.1", "agents")
        create_skill(home / ".claude" / "skills" / "ssh-skill", "3.3.0", "claude")
        create_skill(codex_home / "skills" / "ssh-skill", "3.3.1", "codex")
        create_skill(project / ".codex" / "skills" / "ssh-skill", "4.0.0", "project")

        copies = discover_skill_copies(
            current_root=current,
            home=home,
            project_root=project,
            env={
                "SSH_SKILL_ROOT": str(explicit),
                "CODEX_HOME": str(codex_home),
            },
        )

        self.assertEqual(current.resolve(), copies[0].path)
        self.assertEqual("current", copies[0].source)
        self.assertEqual(6, len(copies))
        self.assertEqual(len(copies), len({copy.path for copy in copies}))
        self.assertGreater(len({copy.sha256 for copy in copies}), 1)
        self.assertIn("3.3.1", {copy.version for copy in copies})

    def test_hash_ignores_generated_cache_but_detects_runtime_changes(self):
        skill = create_skill(self.root / "skill", "4.0.0", "initial")
        original = hash_skill_surface(skill)
        cache = skill / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "marker.cpython-313.pyc").write_bytes(b"generated")

        self.assertEqual(original, hash_skill_surface(skill))

        (skill / "scripts" / "marker.py").write_text(
            "MARKER = 'changed'\n", encoding="utf-8"
        )
        self.assertNotEqual(original, hash_skill_surface(skill))

    def test_doctor_is_offline_and_does_not_echo_environment_secrets(self):
        current = create_skill(self.root / "source", "4.0.0", "current")
        home = self.root / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        (ssh_dir / "config").write_text("Host example\n", encoding="utf-8")
        calls = []

        def find_openssh(platform_name, env):
            calls.append(("openssh", platform_name))
            return "/usr/bin/ssh"

        def inspect_agent(platform_name, env):
            calls.append(("agent", platform_name))
            return AgentStatus(True, True, True, "1 key loaded", [])

        context = DoctorContext(
            current_root=current,
            home=home,
            project_root=self.root / "project",
            env={"PASSWORD": "super-secret"},
            platform_name="linux",
            python_version=(3, 13, 2),
            python_executable="/usr/bin/python3",
            openssh_finder=find_openssh,
            agent_inspector=inspect_agent,
        )

        result = run_doctor(context)
        serialized = json.dumps(result)

        self.assertTrue(result["success"])
        self.assertEqual([("openssh", "linux"), ("agent", "linux")], calls)
        self.assertNotIn("super-secret", serialized)
        self.assertTrue(result["data"]["ssh_config"]["exists"])
        self.assertEqual(str(current.resolve()), result["data"]["selected_skill_root"])

    def test_local_probe_failures_become_redacted_warnings(self):
        current = create_skill(self.root / "source", "4.0.0", "current")

        def fail_probe(platform_name, env):
            raise RuntimeError("password=super-secret")

        context = DoctorContext(
            current_root=current,
            home=self.root / "home",
            project_root=None,
            env={},
            platform_name="linux",
            python_version=(3, 12, 1),
            python_executable="/usr/bin/python3",
            openssh_finder=fail_probe,
            agent_inspector=fail_probe,
        )

        result = run_doctor(context)
        serialized = json.dumps(result)

        self.assertTrue(result["success"])
        self.assertNotIn("super-secret", serialized)
        self.assertIn("openssh_discovery_failed", result["meta"]["warnings"])
        self.assertIn("ssh_agent_inspection_failed", result["meta"]["warnings"])

    def test_cli_doctor_uses_real_handler_and_one_json_document(self):
        current = create_skill(self.root / "source", "4.0.0", "current")
        context = DoctorContext(
            current_root=current,
            home=self.root / "home",
            project_root=self.root / "project",
            env={},
            platform_name="linux",
            python_version=(3, 11, 9),
            python_executable="/usr/bin/python3",
            openssh_finder=lambda platform_name, env: "/usr/bin/ssh",
            agent_inspector=lambda platform_name, env: AgentStatus(
                False, False, False, "not running", ["start ssh-agent"]
            ),
        )
        stdout = io.StringIO()

        with patch("doctor.create_default_context", return_value=context):
            code = main(["doctor", "--json"], stdout=stdout)

        self.assertEqual(0, code)
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        result = json.loads(stdout.getvalue())
        self.assertEqual("doctor", result["operation"])
        self.assertNotEqual("doctor_unavailable", (result.get("error") or {}).get("code"))


if __name__ == "__main__":
    unittest.main()
