# Windows Invocation

Use PowerShell or the Codex/Claude Code process runner. Resolve
`<SSH_SKILL_ROOT>` from the loaded skill before invoking Python.

```powershell
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" doctor --json
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" exec example-host "hostname"
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" upload example-host "C:\Temp\app.zip" "/tmp/app.zip"
```

- Use Windows path syntax only for local paths and the script path.
- Keep remote paths in POSIX form.
- Do not prepend environment assignments intended for MSYS or Bash.
- Do not wrap the call in a second PowerShell command string.
- The runtime prefers `%SystemRoot%\System32\OpenSSH\ssh.exe`, then PATH.
- The local agent check uses the Windows `ssh-agent` service and `ssh-add.exe`.
- Parse stdout as the single result and stderr as optional progress. Both use
  ASCII-safe JSON, including when local or remote paths contain non-ASCII text.

If `python` is unavailable, select an installed Python 3.10-3.13 executable
without changing the SSH operation or adding a shell wrapper.
