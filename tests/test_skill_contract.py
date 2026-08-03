from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"
REFERENCE_NAMES = (
    "commands.md",
    "platforms-windows.md",
    "platforms-macos.md",
    "platforms-linux.md",
    "safety.md",
)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    raw_frontmatter, body = text[4:].split("\n---\n", 1)
    values = {}
    for line in raw_frontmatter.splitlines():
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values, body


class SkillContractTests(unittest.TestCase):
    def test_frontmatter_is_v4_and_dual_agent_discoverable(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        frontmatter, _body = parse_frontmatter(text)

        self.assertEqual("ssh-skill", frontmatter["name"])
        self.assertEqual("4.0.0", frontmatter["version"])
        self.assertIn("allowed-tools", frontmatter)
        self.assertIn("keywords", frontmatter)
        description = frontmatter["description"]
        self.assertTrue(description.startswith("Use when"))
        self.assertIn("DO NOT use for", description)
        self.assertLess(len(description), 1024)
        for trigger in ("服务器", "远程", "跳板机", "上传", "下载"):
            self.assertIn(trigger, description)

    def test_core_contract_is_compact_english_with_chinese_triggers(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        _frontmatter, body = parse_frontmatter(text)
        line_count = len(text.splitlines())

        self.assertGreaterEqual(line_count, 150)
        self.assertLessEqual(line_count, 200)
        self.assertIn("# SSH Skill v4", body)
        self.assertIn("Codex", body)
        self.assertIn("Claude Code", body)
        self.assertIn("DO NOT use for", body)
        self.assertIn("<SSH_SKILL_ROOT>/scripts/ssh_skill.py", body)
        self.assertIn("Do not run doctor before every operation", body)
        self.assertIn("outcome_unknown", body)
        self.assertLess(len(re.findall(r"[\u4e00-\u9fff]", body)), 80)

    def test_contract_has_no_runtime_specific_hardcoded_entrypoint(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        _frontmatter, body = parse_frontmatter(text)

        forbidden = (
            "~/.claude/skills/ssh-skill/scripts",
            ".agents/skills/ssh-skill/scripts",
            ".codex/skills/ssh-skill/scripts",
            "MSYS_NO_PATHCONV",
            "powershell -Command",
            "bash -c",
            "zsh -c",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, body)

    def test_reference_files_exist_and_are_linked_from_contract(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        for name in REFERENCE_NAMES:
            with self.subTest(reference=name):
                path = ROOT / "references" / name
                self.assertTrue(path.is_file())
                self.assertIn(f"references/{name}", text)

    def test_platform_examples_use_the_unified_cli(self):
        windows = (ROOT / "references" / "platforms-windows.md").read_text(
            encoding="utf-8"
        )
        macos = (ROOT / "references" / "platforms-macos.md").read_text(
            encoding="utf-8"
        )
        linux = (ROOT / "references" / "platforms-linux.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'python "<SSH_SKILL_ROOT>\\scripts\\ssh_skill.py" doctor --json',
            windows,
        )
        for text in (macos, linux):
            self.assertIn(
                'python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" doctor --json',
                text,
            )
        for text in (windows, macos, linux):
            self.assertIn("example-host", text)
            self.assertNotIn("MSYS_NO_PATHCONV", text)


if __name__ == "__main__":
    unittest.main()
