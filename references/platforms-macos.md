# macOS Invocation

Resolve `<SSH_SKILL_ROOT>` from the loaded skill, then use the local POSIX path.

```bash
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" doctor --json
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" exec example-host "hostname"
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" download example-host "/var/log/app.log" "./app.log"
```

- The runtime prefers `/usr/bin/ssh` and falls back to PATH discovery.
- Keep local and remote path arguments separate.
- Keep the remote command as one process argument.
- Do not use a nested shell command string.
- `SSH_AUTH_SOCK` and `/usr/bin/ssh-add` provide local agent diagnostics.
- Parse the single stdout result separately from optional real-time JSONL
  progress on stderr.

Use Python 3.10-3.13. The skill does not require Homebrew when a compatible
Python and the system OpenSSH client are already available.
