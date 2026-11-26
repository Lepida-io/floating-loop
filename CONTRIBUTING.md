# Contributing to floating-loop

This repository is **floating-loop**, a native Google Antigravity (`agy`) plugin that forks and extends the Ralph Loop pattern into a unified autonomous loop and multi-agent swarm toolkit.

## Repository Layout

The repository root **is** the Antigravity plugin (`plugin.json`, `hooks.json`, `rules/AGENTS.md`, `skills/`, `scripts/`, `assets/logo.svg`), so it can be validated with `agy plugin validate . --strict` and installed via `agy plugin install`. Local in-repo development discovers root `skills/` via `.agents/skills.json` with zero file duplication.

- `scripts/loop_core.py` — Stdlib-only Python 3 shared control plane (session store, Antigravity `transcript.jsonl` + `transcript_full.jsonl` parser, workspace fingerprinting, 3-state circuit breaker, PRD validator, Kahn DAG cycle checker, wave scheduler, metric evaluator).
- `scripts/stop-hook.py` — Antigravity `Stop` hook (camelCase stdin -> `{"decision": "continue", "reason": "..."}`, Dual-Gate `verifyCmd`, context-token ceiling, circuit breaker).
- `scripts/guard-hook.py` — Active-loop `PreToolUse` safety guard (empty stdout passthrough outside active loops or on safe tools; only emits `{"decision": "deny", ...}` on blocked calls because Antigravity treats `{}` without `decision` as DENY).
- `scripts/loop-ctl.py` — CLI controller for `start`, `status`, `cancel`, `reset-cb`, `validate-prd`, `next-story`, `next-wave`, `mark-story`, `archive-check`, `fingerprint`, `eval-metric`, `escalate-effort`.
- `scripts/floating-loop.sh` — Portable bash 3.2+ fresh-context `agy -p --dangerously-skip-permissions` runner (`prd`, `prompt`, and `autoresearch` keep-or-revert modes).
- `scripts/workflows/floating_swarm.py` — Self-contained Antigravity `run_workflow` script (parallel `workspace="share"` DAG waves + Adversarial Verifier + post-merge backpressure).
- `skills/` — Four native plugin skills: `floating-loop`, `floating-swarm`, `prd`, `prd-to-loop`.

## Key Antigravity Contracts to Preserve

1. **Hook CWD vs. Workspace CWD**: Antigravity runs plugin hook scripts with CWD = `PLUGIN_ROOT` (where `hooks.json` lives). Always resolve workspace runtime files (`.floating-loop/`) from stdin's `cwd` (or `workspacePaths[0]`).
2. **Antigravity Transcript Schema**: Assistant turns are JSONL records with `source: "MODEL"`, `type: "PLANNER_RESPONSE"`, visible text in `content`, reasoning in `thinking` (never inspect `thinking` for `<promise>`), token counts in `input_tokens` + `cache_read_tokens`, and `truncated_fields` backed by sibling `transcript_full.jsonl`.
3. **Plugin Rules Location**: Keep plugin rules inside `rules/AGENTS.md` (Antigravity ignores a root-level `AGENTS.md` in native plugins).
4. **Zero External Dependencies**: Keep `scripts/*.py` and `scripts/floating-loop.sh` dependent solely on Python 3.8+ stdlib, bash 3.2+, and `git` (no `jq`, `perl`, or GNU-only `sed`).
5. **Reference Material**: Upstream Ralph repos remain cloned under `references/` (gitignored) for historical comparison.

## Verification Commands

Run after any changes:

```bash
bash tests/run_all.sh
```
