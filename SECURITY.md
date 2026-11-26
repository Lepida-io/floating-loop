# Security Policy

## Scope

`floating-loop` orchestrates autonomous AI coding loops using Google Antigravity (`agy`). Because autonomous loops can execute shell commands and modify repository files across multiple iterations, safety boundaries are a core part of the design.

## Built-in Safety Guardrails

- **Active-Loop `PreToolUse` Guard (`scripts/guard-hook.py`)**: Blocks catastrophic `rm -rf` targeting `/` or `$HOME`, `git push --force` / `-f`, unauthorized `git reset --hard`, and direct tampering with `.floating-loop/sessions/` state files while a loop is active. Outside active loops, the hook is a zero-overhead passthrough.
- **Dual-Gate Verification (`scripts/stop-hook.py`, `scripts/floating-loop.sh`)**: Independently runs your `verifyCommand` before accepting `<promise>...</promise>` or `passes: true`, preventing premature or fabricated completion claims.
- **3-State Circuit Breaker**: Halts automatically on consecutive zero-progress stalls or repeated identical errors.
- **PRD Anti-Tampering**: Snapshots `prd.json` before each iteration and restores it if story IDs are deleted, corrupted, or non-target stories are marked complete.

## Safe Usage Recommendations

- **Mode B (`scripts/floating-loop.sh`)** invokes `agy --dangerously-skip-permissions` so unattended iterations do not block on interactive prompts. Only run unattended loops in isolated repositories, feature branches, containers, or sandboxes where automated edits are acceptable.
- Always set `--max-iterations` and `--cb-threshold` to bound runtime and token spend.
- Review diffs (`git diff` / `git log`) before pushing or merging loop-generated branches.

## Reporting a Vulnerability

If you discover a security issue (such as a guardrail bypass, command injection in shell/Python scripts, or state-file tampering vector), please **do not** open a public issue. Instead, report it privately via GitHub Security Advisories ("Report a vulnerability" in the repository's **Security** tab).

Please include:
- A description of the issue and affected file(s)
- Minimal reproduction steps
- Potential impact and any suggested mitigation
