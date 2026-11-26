# Floating Loop — Autonomous Execution Rules

When operating inside an active **Floating Loop** (in-session `Stop` hook, external `agy -p` loop, `run_workflow` DAG swarm, or `/swarm` board):

## 1. Promise Integrity (No Reward-Hacking)
- **Never** output `<promise>...</promise>` unless EVERY acceptance criterion and verification command genuinely passes.
- Never delete, skip, weaken, or mock away failing tests to force a green check.
- Never claim a story is complete (`passes: true`) without running its verification command and inspecting real command output.
- If stuck or blocked, report the exact failure truthfully and try a fundamentally different approach on the next iteration — never lie to exit the loop.

## 2. Mechanical Backpressure First
- Trust compiler output, typecheckers, linters, unit/integration tests, and browser checks over intuition.
- When a Dual-Gate verification check or Adversarial Verifier rejects work, fix the root cause before re-asserting completion.

## 3. Disk-First Memory & Context Hygiene
- Treat disk files (`prd.json`, `progress.txt`, `results.tsv`, `AGENTS.md`, and git history) as the durable source of truth across iterations and workers.
- Consolidate general, reusable discoveries in the `## Codebase Patterns` section at the top of `progress.txt` and in nearby `AGENTS.md` files.
- Never directly edit `.floating-loop/sessions/*.json` state files; always use `scripts/loop-ctl.py` for loop control (`status`, `cancel`, `reset-cb`).

## 4. Scoped Atomic Work
- Work on **one story** (or one focused hypothesis in autoresearch mode) per iteration.
- Keep diffs tight, commit verified changes atomically, and leave the working tree clean before finishing an iteration.
