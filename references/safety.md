# SSH Safety Contract

Read this reference for credentials, host keys, production scope, irreversible
configuration changes, agent forwarding, or uncertain execution outcomes.

## Host Identity

- Default OpenSSH behavior is `StrictHostKeyChecking=accept-new` with the normal
  `known_hosts` file.
- Paramiko persists a newly accepted key and rejects a conflicting known key.
- Never bypass host-key verification as routine troubleshooting.
- A changed key requires independent verification before local records change.

## Credentials

- Prefer key authentication and the platform SSH agent.
- Never ask the user to paste private-key content into a command or chat.
- New plaintext password metadata is rejected.
- Existing legacy password metadata is read-only compatibility data and must be
  redacted from results, exports, diagnostics, and errors.
- Askpass helper files contain no password and are removed on success or error.

## Retry And Unknown Outcomes

- A request that failed before sending bytes may use a safe direct fallback.
- Once any request bytes were sent, a transport failure can be
  `outcome_unknown`.
- OpenSSH, Paramiko, and cluster timeouts after dispatch set
  `retryable=false`; timeout alone is never evidence that replay is safe.
- Preserve the request ID, stop, and verify remote state by a separate read-only
  operation selected with the user.
- Never infer idempotency from command text and never replay automatically.

## Multi-Host And Production Scope

- Preview resolves aliases without opening SSH connections.
- Multi-host execution requires `--apply`.
- Any production target also requires `--confirm-production`.
- Review the exact targets and count, not only the filter expression.
- On partial failure, do not rerun hosts that already succeeded.

## Configuration Mutation

- Create and update must not add plaintext passwords.
- Delete previews the Host block without exposing secret comments.
- Applied delete creates a timestamped backup, writes a replacement file, and
  atomically swaps it into place.
- Never rewrite a user's SSH config merely to normalize formatting.

## Agent Forwarding

- Forwarding is disabled by default.
- Enable `--allow-agent-forwarding` only with explicit user intent and a trusted
  source host.
- The forwarded agent must be attached to the same channel that runs the direct
  transfer command.
- Prefer credentials already configured on the source host where practical.

## Output And Secrets

- stdout contains one JSON result capped at 256 KiB; optional progress is
  bounded, ASCII-safe JSONL on stderr.
- Recursive transfer details retain at most 100 head/tail samples while the
  aggregate count preserves the actual number of files.
- Preserve structured error codes and warnings in summaries.
- Do not print environment mappings, password values, private keys, or askpass
  material.
- A truncated result is not a reason to repeat a mutation.
