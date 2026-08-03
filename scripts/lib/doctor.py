from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import sys
from typing import Callable, Mapping

from platform_adapter import AgentStatus, find_openssh, inspect_ssh_agent, normalize_platform
from result_protocol import success_result


OpenSSHFinder = Callable[[str, Mapping[str, str]], str | None]
AgentInspector = Callable[[str, Mapping[str, str]], AgentStatus]
_VERSION_PATTERN = re.compile(
    r"^version:\s*['\"]?([^'\"\s#]+)", re.MULTILINE
)
_SURFACE_DIRECTORIES = ("scripts", "references")
_IGNORED_DIRECTORIES = {"__pycache__"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True)
class SkillCopy:
    path: Path
    source: str
    version: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "source": self.source,
            "version": self.version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DoctorContext:
    current_root: Path
    home: Path
    project_root: Path | None
    env: Mapping[str, str]
    platform_name: str
    python_version: tuple[int, int, int]
    python_executable: str
    openssh_finder: OpenSSHFinder = find_openssh
    agent_inspector: AgentInspector = inspect_ssh_agent


def _surface_files(root: Path) -> list[Path]:
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        raise FileNotFoundError(f"SKILL.md not found under {root}")

    files = [skill_file]
    for directory_name in _SURFACE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for current, directories, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if name not in _IGNORED_DIRECTORIES
                and not (current_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = current_path / filename
                if path.is_symlink() or path.suffix.lower() in _IGNORED_SUFFIXES:
                    continue
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def hash_skill_surface(root: Path) -> str:
    resolved = Path(root).resolve()
    digest = hashlib.sha256(b"ssh-skill-surface-v1\0")
    for path in _surface_files(resolved):
        relative = path.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _read_version(root: Path) -> str:
    try:
        text = (root / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = _VERSION_PATTERN.search(text)
    return match.group(1) if match else "unknown"


def _candidate_roots(
    current_root: Path,
    home: Path,
    project_root: Path | None,
    env: Mapping[str, str],
):
    yield "current", current_root
    if env.get("SSH_SKILL_ROOT"):
        yield "environment", Path(env["SSH_SKILL_ROOT"])
    if env.get("CODEX_HOME"):
        yield "codex-home", Path(env["CODEX_HOME"]) / "skills" / "ssh-skill"
    for runtime in ("agents", "codex", "claude"):
        yield f"user-{runtime}", home / f".{runtime}" / "skills" / "ssh-skill"
    if project_root is not None:
        for runtime in ("agents", "codex", "claude"):
            yield (
                f"project-{runtime}",
                project_root / f".{runtime}" / "skills" / "ssh-skill",
            )


def discover_skill_copies(
    current_root: Path,
    home: Path,
    project_root: Path | None,
    env: Mapping[str, str],
) -> list[SkillCopy]:
    copies = []
    seen = set()
    for source, candidate in _candidate_roots(
        Path(current_root), Path(home),
        None if project_root is None else Path(project_root), env
    ):
        resolved = candidate.expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen or not (resolved / "SKILL.md").is_file():
            continue
        seen.add(key)
        copies.append(
            SkillCopy(
                path=resolved,
                source=source,
                version=_read_version(resolved),
                sha256=hash_skill_surface(resolved),
            )
        )
    return copies


def create_default_context(
    current_root: Path,
    project_root: Path | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> DoctorContext:
    version = sys.version_info
    return DoctorContext(
        current_root=Path(current_root),
        home=Path.home() if home is None else Path(home),
        project_root=Path.cwd() if project_root is None else Path(project_root),
        env=dict(os.environ if env is None else env),
        platform_name=normalize_platform(platform_name),
        python_version=(version.major, version.minor, version.micro),
        python_executable=sys.executable,
    )


def run_doctor(context: DoctorContext) -> dict:
    environment = dict(context.env)
    warnings = []

    try:
        openssh_path = context.openssh_finder(context.platform_name, environment)
        openssh = {"available": bool(openssh_path), "path": openssh_path}
    except Exception as exc:
        openssh = {
            "available": False,
            "path": None,
            "error": f"{type(exc).__name__}: OpenSSH discovery failed",
        }
        warnings.append("openssh_discovery_failed")

    try:
        agent = context.agent_inspector(context.platform_name, environment)
    except Exception as exc:
        agent = AgentStatus(
            False,
            False,
            False,
            f"{type(exc).__name__}: ssh-agent inspection failed",
            [],
        )
        warnings.append("ssh_agent_inspection_failed")

    copies = discover_skill_copies(
        context.current_root,
        context.home,
        context.project_root,
        environment,
    )
    versions = {copy.version for copy in copies}
    hashes = {copy.sha256 for copy in copies}
    ssh_config_path = context.home / ".ssh" / "config"
    python_version = ".".join(str(part) for part in context.python_version)
    supported_python = (3, 10) <= context.python_version[:2] <= (3, 13)

    if not openssh["available"]:
        warnings.append("openssh_not_found")
    if not copies:
        warnings.append("skill_copy_not_found")

    return success_result(
        "doctor",
        {
            "platform": context.platform_name,
            "python": {
                "version": python_version,
                "executable": context.python_executable,
                "supported": supported_python,
            },
            "openssh": openssh,
            "ssh_agent": asdict(agent),
            "ssh_config": {
                "path": str(ssh_config_path),
                "exists": ssh_config_path.is_file(),
                "readable": ssh_config_path.is_file()
                and os.access(ssh_config_path, os.R_OK),
            },
            "selected_skill_root": str(copies[0].path) if copies else None,
            "skill_copies": [copy.to_dict() for copy in copies],
            "version_drift": len(versions) > 1,
            "content_drift": len(hashes) > 1,
        },
        platform=context.platform_name,
        transport="local",
        warnings=sorted(set(warnings)),
    )
