from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from tests import support  # noqa: F401
from validate_release import main, validate_release


ROOT = Path(__file__).resolve().parents[1]


class ReleaseValidationTests(unittest.TestCase):
    def test_validator_rejects_release_contract_violations(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            scripts = root / "scripts"
            scripts.mkdir()
            evals = root / "evals"
            evals.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: ssh-skill\nversion: 4.0.0\n"
                "description: Use when SSH is required. DO NOT use for local work.\n"
                "---\n# Skill\n[missing](references/missing.md)\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "# SSH Skill v3.9.0\n", encoding="utf-8"
            )
            (root / "README_EN.md").write_text(
                "# SSH Skill v4.0.0\n", encoding="utf-8"
            )
            (evals / "workflows.json").write_text(
                json.dumps({"version": "4.0.0"}), encoding="utf-8"
            )
            (scripts / "ssh_skill.py").write_text(
                "print('placeholder')\n", encoding="utf-8"
            )
            (scripts / "bad.py").write_text(
                "import subprocess\n"
                "subprocess.run(['powershell', '-Command', 'Get-Service'], shell=True)\n"
                "OPTION = 'StrictHostKeyChecking=no'\n",
                encoding="utf-8",
            )

            issues = validate_release(root, check_cli_help=False)
            codes = {issue.code for issue in issues}

        self.assertIn("forbidden_shell_wrapper", codes)
        self.assertIn("unsafe_host_key_bypass", codes)
        self.assertIn("version_mismatch", codes)
        self.assertIn("broken_document_link", codes)

    def test_repository_passes_release_validation(self):
        issues = validate_release(ROOT)

        self.assertEqual([], issues)

    def test_validator_rejects_fstring_backslash_for_python_310(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "future_syntax.py").write_text(
                "def stream(line):\n"
                "    yield f\"[STDERR] {line.rstrip('\\n')}\"\n",
                encoding="utf-8",
            )

            issues = validate_release(root, check_cli_help=False)

        self.assertIn(
            ("python_syntax_error", "scripts/future_syntax.py"),
            {(issue.code, issue.path) for issue in issues},
        )

    def test_cli_emits_one_json_document(self):
        stdout = io.StringIO()

        code = main(["--root", str(ROOT)], stdout=stdout)

        self.assertEqual(0, code)
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["success"])
        self.assertEqual([], result["data"]["issues"])

    def test_ci_covers_supported_os_and_python_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        for value in (
            "windows-latest",
            "macos-latest",
            "ubuntu-latest",
            '"3.10"',
            '"3.11"',
            '"3.12"',
            '"3.13"',
            "python -m unittest discover -s tests -v",
            "python scripts/validate_release.py --root .",
        ):
            with self.subTest(value=value):
                self.assertIn(value, workflow)


if __name__ == "__main__":
    unittest.main()
