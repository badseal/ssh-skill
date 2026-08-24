from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
README_PATHS = (ROOT / "README.md", ROOT / "README_EN.md")
HELP_PATTERN = re.compile(
    r"^python scripts/ssh_skill\.py(?P<arguments>(?: [a-z-]+)*) --help$",
    re.MULTILINE,
)
REQUIRED_TERMS = (
    "4.0.0",
    "Codex",
    "Claude Code",
    "Windows",
    "macOS",
    "Linux",
    "outcome_unknown",
    "--apply",
    "--confirm-production",
    "--legacy-json",
    "accept-new",
)


class DocumentedCommandTests(unittest.TestCase):
    def test_readmes_share_v4_platform_and_safety_contract(self):
        texts = [path.read_text(encoding="utf-8") for path in README_PATHS]
        for path, text in zip(README_PATHS, texts):
            with self.subTest(path=path.name):
                self.assertIn("SSH Skill v4.0.0", text)
                for term in REQUIRED_TERMS:
                    self.assertIn(term, text)
                self.assertNotIn("MSYS_NO_PATHCONV", text)
                self.assertNotIn("password: your-password", text)

        commands = [set(HELP_PATTERN.findall(text)) for text in texts]
        self.assertEqual(commands[0], commands[1])
        self.assertEqual(
            {"", " exec", " upload", " download", " transfer", " cluster",
             " config", " tunnel", " daemon", " doctor"},
            commands[0],
        )

    def test_all_documented_help_commands_are_callable_without_network(self):
        text = README_PATHS[0].read_text(encoding="utf-8")
        for arguments in sorted(set(HELP_PATTERN.findall(text))):
            argv = [sys.executable, str(ROOT / "scripts" / "ssh_skill.py")]
            argv.extend(arguments.split())
            argv.append("--help")
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    argv,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("usage:", completed.stdout)

    def test_readmes_link_the_contract_and_all_references(self):
        required_links = (
            "SKILL.md",
            "references/commands.md",
            "references/platforms-windows.md",
            "references/platforms-macos.md",
            "references/platforms-linux.md",
            "references/safety.md",
        )
        for path in README_PATHS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for relative in required_links:
                    self.assertIn(relative, text)
                    self.assertTrue((ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()
