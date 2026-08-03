---
name: ssh-skill
version: 4.0.0
description: "Use when a task requires SSH or SCP/SFTP behavior, a remote server, server alias/IP/hostname/user@host, bastion or jump-host access, remote command execution, upload/download, server transfer, deployment, tunnels, port forwarding, or remote internal services; Chinese triggers include 服务器, 远程, 连接, 登录, 上传, 下载, 部署, 跳板机, 隧道. DO NOT use for local shell commands, localhost/current-machine work, local Docker, browser downloads, Git remotes, or direct non-SSH database access."
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,SCP,SFTP,server,remote,bastion,jump-host,upload,download,transfer,tunnel,deploy,服务器,远程,连接,登录,上传,下载,部署,跳板机,隧道
---

# SSH Skill v4

## Purpose

Use this contract for SSH-mediated work in Codex and Claude Code on Windows,
macOS, and Linux. The deterministic Python CLI owns transport, retries, output
limits, host-key policy, and compatibility behavior.

Chinese intent examples: 连接服务器, 远程执行, 上传文件, 下载日志, 通过跳板机,
建立隧道. Continue the user-facing conversation in the user's language.

## Scope

Use for:

- Commands or inspections on a remote host.
- Upload, download, SFTP, SCP-equivalent, or server-to-server transfer.
- SSH aliases, keys, agents, bastions, ProxyJump, tunnels, and port forwarding.
- Remote deployment, remote database access through a tunnel, and cluster work.

DO NOT use for:

- Local commands, localhost, current-machine services, or local Docker.
- Browser or HTTP downloads that do not traverse SSH.
- Git remote operations; use the relevant Git workflow instead.
- Direct database connections that do not require an SSH tunnel.

## Hard Rules

1. Resolve the root containing this loaded `SKILL.md`; call it
   `<SSH_SKILL_ROOT>` for the current task.
2. Invoke `<SSH_SKILL_ROOT>/scripts/ssh_skill.py`; do not construct raw
   `ssh`, `scp`, `sftp`, or `rsync` commands.
3. Pass arguments as discrete process arguments. Never add a local shell
   wrapper around remote command text.
4. Parse the one JSON document on stdout before deciding the next action.
5. Treat `outcome_unknown` as a stop condition. Report its request ID and do
   not repeat the command automatically.
6. Do not retry authentication failures, destructive commands, or partially
   completed multi-host operations without new evidence and user intent.
7. Never expose passwords, private-key content, tokens, or askpass data.
8. Preview multi-host and irreversible operations before applying them.

## Resolve The Skill Root Once

- Prefer the directory of the `SKILL.md` that activated this skill.
- If the runtime provides an explicit loaded-skill path, use it directly.
- Do not assume `.agents`, `.codex`, `.claude`, or any user-home path.
- Run `doctor --json` when the root is ambiguous, a dependency is missing, or
  multiple installed copies may have drifted.
- Do not run doctor before every operation. Reuse the resolved root within the
  same task unless the environment changes.
- If doctor reports several copies, keep using the currently loaded copy and
  disclose version or content drift; never copy or overwrite automatically.

Read exactly one platform guide when invocation syntax is needed:

- Windows: [references/platforms-windows.md](references/platforms-windows.md)
- macOS: [references/platforms-macos.md](references/platforms-macos.md)
- Linux: [references/platforms-linux.md](references/platforms-linux.md)

## Standard Workflow

1. Classify the request as exec, transfer, cluster, config, tunnel, or doctor.
2. Resolve `<SSH_SKILL_ROOT>` once.
3. Identify the SSH alias. If it is unknown, use `config list-servers` or
   `config find`; do not guess from an IP or a similar name.
4. Separate read-only discovery from mutation. Ask for missing target or scope
   only when it materially changes the operation.
5. Preview multi-host, production, delete, or overwrite work.
6. Invoke the unified CLI once with the smallest complete operation.
7. Parse `success`, `data`, `error`, and `meta`, then report the actual result.

Do not add connection tests, repeated status calls, or daemon restarts unless
the result indicates that one of them is necessary.

## Invocation Contract

Use the platform-appropriate Python executable and path syntax:

```text
<PYTHON> "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" <operation> <arguments>
```

- Keep remote paths in POSIX form, such as `/var/log/app.log`.
- Keep the entire remote command as one argument.
- Prefer one remote command that gathers closely related read-only facts.
- Keep dependent or separately risky mutations as separate operations.
- Use `--progress` only when progress events are useful to the user.
- Use `--no-progress` when a clean noninteractive stderr is required.

See [references/commands.md](references/commands.md) for complete syntax.

## Operations

| Operation | Use |
| --- | --- |
| `exec` | Run one command on one SSH alias. |
| `upload` | Copy a local file or directory to a remote POSIX path. |
| `download` | Copy a remote file or directory to a local path. |
| `transfer` | Move data between two SSH aliases. |
| `cluster` | Preview or apply one command to resolved host targets. |
| `config` | List, find, create, update, delete, or export SSH aliases. |
| `tunnel` | Start, list, inspect, or stop local SSH tunnels. |
| `daemon` | Inspect or manage the optional connection daemon. |
| `doctor` | Diagnose local runtime, copies, OpenSSH, agent, and config. |

## Execution And Transfer

- `exec` uses daemon reuse only where safe; `--no-daemon` is an explicit
  diagnostic escape hatch, not a routine first step.
- A timeout after request transmission can mean the remote side executed the
  command. On `outcome_unknown`, stop and ask the user how to verify state.
- For upload/download, preserve the distinction between local native paths and
  remote POSIX paths. Use `--recursive` only for directories.
- Use `--resume` when continuing a known interrupted transfer.
- Server-to-server direct mode may expose an SSH agent to the source host.
  Agent forwarding remains off unless `--allow-agent-forwarding` is explicit.

## Cluster And Configuration Gates

- `cluster` without `--apply` is a target preview and opens no SSH connection.
- Review `targets`, `target_count`, and `production_targets` from the preview.
- Add `--apply` only after the requested target set is confirmed.
- Add `--confirm-production` when any target is classified as production.
- A config delete without `--apply` is a preview. Applied deletion creates a
  timestamped backup before atomic replacement.
- Do not put new plaintext passwords into SSH configuration metadata.

## Tunnels And Daemons

- Return the allocated local port and tunnel ID after starting a tunnel.
- Bind local forwarding only according to the tool's safe default.
- Prefer status or list before stopping a tunnel when the identifier is vague.
- Do not restart a daemon merely because a second command follows the first.
  The daemon is designed for reuse across compatible calls.

## Result Contract

The CLI writes exactly one versioned JSON document to stdout:

- `schema_version`: result schema, currently `1.0`.
- `success`: whether the requested operation completed successfully.
- `operation`: stable operation identifier.
- `data`: bounded operation-specific data.
- `error`: stable code, message, retryability, and outcome on failure.
- `meta`: request ID, platform, transport, elapsed time, and warnings.

Progress, when explicitly enabled, is bounded JSONL on stderr. Do not merge it
with stdout. If output is truncated, disclose truncation and use the retained
diagnostic tail instead of rerunning solely to obtain more output.

## Safety Defaults

- Host keys default to `accept-new`; changed known keys are rejected.
- Do not disable host-key verification or discard `known_hosts` in routine use.
- Existing legacy passwords may be read with warnings but must stay redacted.
- Prefer keys and the platform SSH agent; never request a private key value.
- Production and multi-host scope require observable confirmation flags.
- Read [references/safety.md](references/safety.md) before credential changes,
  production work, agent forwarding, config deletion, or uncertain outcomes.

## Error Decisions

- `invalid_alias`: list or find aliases once; do not brute-force names.
- `config_not_found`: run doctor, then report the expected local config path.
- Authentication failure: report the method and remediation; do not loop.
- `outcome_unknown`: stop, preserve request ID, and verify state separately.
- Partial cluster failure: report per-host outcomes; do not replay successes.
- Missing dependency: use the relevant platform guide, not another shell.

## Compatibility

- Codex and Claude Code share this file and the same Python CLI.
- Windows, macOS, and Linux share core behavior; only local invocation and
  dependency remediation differ.
- Legacy script filenames remain compatibility entrypoints during v4 migration.
- `--legacy-json` is only for an identified old consumer. New AI calls use the
  v1 envelope and never request legacy output.

## Final Check

- Correct loaded root and platform syntax used.
- Correct alias, remote path, and target scope used.
- Required preview and production confirmation completed.
- One stdout JSON parsed; warnings and request ID preserved.
- No secret exposed and no uncertain operation replayed.
