# SSH Skill v4.0.0

[中文](README.md) | **English**

`ssh-skill` is an SSH workflow skill for Codex and Claude Code. Its unified
Python CLI handles remote execution, file transfer, server-to-server transfer,
clusters, SSH configuration, tunnels, and connection daemons with one result
contract across Windows, macOS, and Linux.

## What Changed In v4

- Every new call enters through `scripts/ssh_skill.py`.
- stdout contains one `schema_version=1.0` JSON document.
- Remote command text is passed as one argument without local PowerShell,
  Bash, or Zsh evaluation.
- Daemon requests use request IDs; `outcome_unknown` is never replayed
  automatically.
- Command output and progress are bounded; progress is off by default when
  noninteractive.
- Host-key checking defaults to `accept-new`; known-key conflicts are rejected.
- Cluster calls preview by default. Execution requires `--apply`, and production
  targets also require `--confirm-production`.
- New plaintext passwords are rejected; legacy passwords are read-only and
  always redacted.
- Agent forwarding is disabled by default.
- Legacy filenames remain migration entrypoints but inherit v4 safety behavior.

## Compatibility

| AI tool | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Codex | Supported | Supported | Supported |
| Claude Code | Supported | Supported | Supported |

The release matrix covers Python 3.10, 3.11, 3.12, and 3.13. Runtime
dependencies are Python, an OpenSSH client, and Paramiko. Python 3.8/3.9 are
best-effort only and are not release gates.

## Skill Root

Treat the directory containing the [loaded SKILL.md](SKILL.md) as
`<SSH_SKILL_ROOT>`. No one directory is the universally correct installation
path.

Common candidates include:

- Codex user scope: `$CODEX_HOME/skills/ssh-skill/` or `.agents/skills/ssh-skill/`
- Codex project scope: `.codex/skills/ssh-skill/` or `.agents/skills/ssh-skill/`
- Claude Code user or project scope: `.claude/skills/ssh-skill/`

`doctor` reports versions and content hashes for the current and candidate
copies. It never copies, overwrites, or silently selects another copy. Resolve
the root once per task; do not run doctor before each operation.

## Platform Invocation

Windows PowerShell:

```powershell
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" doctor --json
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" exec example-host "hostname"
```

macOS / Linux:

```bash
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" doctor --json
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" exec example-host "hostname"
```

Remote paths remain POSIX paths on all three systems. Do not add a local shell
wrapper.

## Common Commands

These examples assume the current directory is the source root:

```text
python scripts/ssh_skill.py doctor --json
python scripts/ssh_skill.py config list-servers
python scripts/ssh_skill.py exec example-host "uname -a"
python scripts/ssh_skill.py upload example-host ./app.tar.gz /tmp/app.tar.gz
python scripts/ssh_skill.py download example-host /var/log/app.log ./app.log
python scripts/ssh_skill.py transfer source-host /data/file destination-host /backup/file
python scripts/ssh_skill.py tunnel start example-host --remote-port 5432
```

See [references/commands.md](references/commands.md) for full syntax.

## Cluster Confirmation Gate

Preview without opening an SSH connection:

```text
python scripts/ssh_skill.py cluster "uptime" --environment production
```

Apply after reviewing the targets:

```text
python scripts/ssh_skill.py cluster "uptime" --environment production --apply --confirm-production
```

Do not infer actual scope from filters. Review `targets`, `target_count`, and
`production_targets` before applying.

## Result Protocol

Success and failure use one envelope:

```json
{
  "schema_version": "1.0",
  "success": true,
  "operation": "exec",
  "data": {},
  "error": null,
  "meta": {
    "request_id": null,
    "platform": "windows",
    "transport": "openssh",
    "elapsed_ms": 120,
    "warnings": []
  }
}
```

When `error.code=outcome_unknown`, the command may have executed remotely.
Preserve the request ID, stop automatic retry, and use a separate read-only
check to verify state.

## Safety Boundary

- Do not construct raw `ssh`, `scp`, `sftp`, or `rsync` commands.
- Do not disable host-key checking or discard `known_hosts` in routine use.
- Do not emit passwords, private keys, tokens, or askpass data.
- Do not connect to a multi-host target set before preview.
- Do not apply production cluster work or config deletion without confirmation.
- Do not repeat a mutation merely because output was truncated.

See [references/safety.md](references/safety.md) for the full contract.

## Legacy Entrypoint Migration

`ssh_execute.py`, `ssh_upload.py`, `ssh_download.py`,
`ssh_server_transfer.py`, `ssh_config_manager_v3.py`, `ssh_tunnel.py`, and
`ssh_daemon.py` remain available. Their default output is the v4 envelope.

Use `--legacy-json` only for an identified old consumer. It converts result
fields but does not restore unsafe host-key, retry, unbounded-output, or cluster
behavior.

## Offline Local Verification

These help commands make no server connection and are executed by tests:

```text
python scripts/ssh_skill.py --help
python scripts/ssh_skill.py exec --help
python scripts/ssh_skill.py upload --help
python scripts/ssh_skill.py download --help
python scripts/ssh_skill.py transfer --help
python scripts/ssh_skill.py cluster --help
python scripts/ssh_skill.py config --help
python scripts/ssh_skill.py tunnel --help
python scripts/ssh_skill.py daemon --help
python scripts/ssh_skill.py doctor --help
```

Run the complete suite:

```text
python -m unittest discover -s tests -v
```

Automated tests do not connect to real servers. Real SSH smoke tests, installed
copy synchronization, push, tags, and releases are separately approved steps.

## Documentation

- AI contract: [SKILL.md](SKILL.md)
- Commands: [references/commands.md](references/commands.md)
- Windows: [references/platforms-windows.md](references/platforms-windows.md)
- macOS: [references/platforms-macos.md](references/platforms-macos.md)
- Linux: [references/platforms-linux.md](references/platforms-linux.md)
- Safety: [references/safety.md](references/safety.md)

## License

MIT License
