#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tokenize
from typing import Iterable, Sequence, TextIO
from urllib.parse import unquote, urlparse


_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR / "lib"))

from result_protocol import error_result, success_result, write_result


EXPECTED_VERSION = "4.0.0"
REQUIRED_FILES = (
    ".github/workflows/ci.yml",
    "SKILL.md",
    "README.md",
    "README_EN.md",
    "evals/workflows.json",
    "scripts/ssh_skill.py",
    "references/commands.md",
    "references/platforms-windows.md",
    "references/platforms-macos.md",
    "references/platforms-linux.md",
    "references/safety.md",
)
CLI_COMMANDS = (
    None,
    "exec",
    "upload",
    "download",
    "transfer",
    "cluster",
    "config",
    "tunnel",
    "daemon",
    "doctor",
)
_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_README_VERSION_PATTERN = re.compile(r"SSH Skill v(\d+\.\d+\.\d+)")
_SHELL_EXECUTABLES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "zsh"}
_SHELL_FLAGS = {"-command", "-c"}
_UNSAFE_HOST_KEY_VALUES = {
    "StrictHostKeyChecking=" + "no",
    "UserKnownHostsFile=" + "/dev/null",
    "UserKnownHostsFile=" + "NUL",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("missing YAML frontmatter delimiters")
    raw, _body = text[4:].split("\n---\n", 1)
    values = {}
    for line in raw.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _validate_required_files(root: Path) -> list[ValidationIssue]:
    return [
        ValidationIssue("missing_required_file", relative, "required file is missing")
        for relative in REQUIRED_FILES
        if not (root / relative).is_file()
    ]


def _validate_frontmatter(root: Path) -> list[ValidationIssue]:
    path = root / "SKILL.md"
    if not path.is_file():
        return []
    try:
        frontmatter = _parse_frontmatter(path)
    except (OSError, ValueError) as exc:
        return [ValidationIssue("invalid_frontmatter", "SKILL.md", str(exc))]

    issues = []
    for field in ("name", "version", "description", "allowed-tools", "keywords"):
        if not frontmatter.get(field):
            issues.append(
                ValidationIssue(
                    "invalid_frontmatter", "SKILL.md", f"missing frontmatter field: {field}"
                )
            )
    if frontmatter.get("name") != "ssh-skill":
        issues.append(
            ValidationIssue("invalid_frontmatter", "SKILL.md", "name must be ssh-skill")
        )
    description = frontmatter.get("description", "")
    if len(description) >= 1024:
        issues.append(
            ValidationIssue(
                "description_too_long", "SKILL.md", "description must be below 1024 characters"
            )
        )
    if description and (
        not description.startswith("Use when") or "DO NOT use for" not in description
    ):
        issues.append(
            ValidationIssue(
                "invalid_frontmatter",
                "SKILL.md",
                "description must include trigger and negative scope",
            )
        )
    return issues


def _validate_versions(root: Path) -> list[ValidationIssue]:
    versions = {}
    skill_path = root / "SKILL.md"
    if skill_path.is_file():
        try:
            versions["SKILL.md"] = _parse_frontmatter(skill_path).get("version")
        except (OSError, ValueError):
            pass
    for name in ("README.md", "README_EN.md"):
        path = root / name
        if path.is_file():
            match = _README_VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
            versions[name] = match.group(1) if match else None
    eval_path = root / "evals" / "workflows.json"
    if eval_path.is_file():
        try:
            versions["evals/workflows.json"] = json.loads(
                eval_path.read_text(encoding="utf-8")
            ).get("version")
        except (OSError, json.JSONDecodeError):
            versions["evals/workflows.json"] = None

    return [
        ValidationIssue(
            "version_mismatch",
            path,
            f"expected {EXPECTED_VERSION}, found {version or 'missing'}",
        )
        for path, version in versions.items()
        if version != EXPECTED_VERSION
    ]


def _markdown_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.md"):
        if any(part in {".git", ".worktrees", "__pycache__"} for part in path.parts):
            continue
        yield path


def _validate_markdown_links(root: Path) -> list[ValidationIssue]:
    issues = []
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for raw_target in _LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlparse(target)
            if parsed.scheme or target.startswith("#"):
                continue
            local_target = unquote(target.split("#", 1)[0])
            if not local_target:
                continue
            resolved = (path.parent / local_target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                exists = False
            else:
                exists = resolved.exists()
            if not exists:
                issues.append(
                    ValidationIssue(
                        "broken_document_link",
                        _relative(root, path),
                        f"broken relative link: {target}",
                    )
                )
    return issues


class _RuntimeVisitor(ast.NodeVisitor):
    def __init__(self, root: Path, path: Path):
        self.root = root
        self.path = path
        self.issues: list[ValidationIssue] = []

    def _add(self, code: str, message: str):
        self.issues.append(
            ValidationIssue(code, _relative(self.root, self.path), message)
        )

    def visit_Call(self, node: ast.Call):
        for keyword in node.keywords:
            if (
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                self._add("forbidden_shell_wrapper", "subprocess call uses shell=True")
        self.generic_visit(node)

    def visit_List(self, node: ast.List):
        self._check_argument_sequence(node.elts)
        self.generic_visit(node)

    def visit_Tuple(self, node: ast.Tuple):
        self._check_argument_sequence(node.elts)
        self.generic_visit(node)

    def _check_argument_sequence(self, elements: Sequence[ast.expr]):
        values = [
            element.value
            for element in elements
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        lowered = {value.lower() for value in values}
        if lowered.intersection(_SHELL_EXECUTABLES) and lowered.intersection(_SHELL_FLAGS):
            self._add(
                "forbidden_shell_wrapper",
                "runtime argument array invokes a local shell command wrapper",
            )

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and node.value in _UNSAFE_HOST_KEY_VALUES:
            self._add(
                "unsafe_host_key_bypass",
                f"forbidden runtime SSH option: {node.value}",
            )


def _uses_python_312_fstring_grammar(source: str) -> bool:
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    if fstring_start is None:
        return False

    fstring_depth = 0
    expression_depth = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == fstring_start:
            fstring_depth += 1
            continue
        if token.type == fstring_end:
            fstring_depth -= 1
            continue
        if not fstring_depth or token.type == fstring_middle:
            continue
        if token.type == tokenize.OP:
            if token.string == "{":
                expression_depth += 1
            elif token.string == "}" and expression_depth:
                expression_depth -= 1
        if expression_depth and "\\" in token.string:
            return True
    return False


def _validate_runtime_patterns(root: Path) -> list[ValidationIssue]:
    issues = []
    scripts = root / "scripts"
    if not scripts.is_dir():
        return issues
    for path in scripts.rglob("*.py"):
        if path.name == "validate_release.py" or "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, tokenize.TokenError) as exc:
            issues.append(
                ValidationIssue(
                    "python_syntax_error", _relative(root, path), f"{type(exc).__name__}: parse failed"
                )
            )
            continue
        if _uses_python_312_fstring_grammar(source):
            issues.append(
                ValidationIssue(
                    "python_syntax_error",
                    _relative(root, path),
                    "f-string expression contains a backslash and requires Python 3.12+",
                )
            )
        visitor = _RuntimeVisitor(root, path)
        visitor.visit(tree)
        issues.extend(visitor.issues)
    return issues


def _validate_cli_help(root: Path) -> list[ValidationIssue]:
    script = root / "scripts" / "ssh_skill.py"
    if not script.is_file():
        return []
    issues = []
    for command_name in CLI_COMMANDS:
        argv = [sys.executable, str(script)]
        if command_name:
            argv.append(command_name)
        argv.append("--help")
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            issues.append(
                ValidationIssue(
                    "cli_help_failed",
                    "scripts/ssh_skill.py",
                    f"{command_name or 'root'} help failed: {type(exc).__name__}",
                )
            )
            continue
        if completed.returncode != 0:
            issues.append(
                ValidationIssue(
                    "cli_help_failed",
                    "scripts/ssh_skill.py",
                    f"{command_name or 'root'} help exited {completed.returncode}",
                )
            )
    return issues


def validate_release(root: Path, *, check_cli_help: bool = True) -> list[ValidationIssue]:
    resolved = Path(root).resolve()
    issues = []
    issues.extend(_validate_required_files(resolved))
    issues.extend(_validate_frontmatter(resolved))
    issues.extend(_validate_versions(resolved))
    issues.extend(_validate_markdown_links(resolved))
    issues.extend(_validate_runtime_patterns(resolved))
    if check_cli_help:
        issues.extend(_validate_cli_help(resolved))
    return sorted(
        set(issues), key=lambda issue: (issue.path, issue.code, issue.message)
    )


def main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description="Validate an ssh-skill release")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    issues = validate_release(args.root)
    data = {"version": EXPECTED_VERSION, "issues": [issue.to_dict() for issue in issues]}
    if issues:
        result = error_result(
            "validate_release",
            code="release_validation_failed",
            message=f"release validation found {len(issues)} issue(s)",
            data=data,
        )
    else:
        result = success_result("validate_release", data)
    write_result(result, stream=stdout)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
