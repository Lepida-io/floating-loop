---
name: floating-swarm
description: >-
  Orchestrate parallel multi-agent Floating Loop swarms using Antigravity's
  native `run_workflow` Python DAG engine (isolated `workspace="share"`
  worktrees + Adversarial Verifier dual-gate) or live `/swarm`
  Coordinator-Worker-Verifier teams. Use when executing a dependency-ordered
  `prd.json` with parallelizable user stories or scaling a Floating Loop across
  multiple concurrent tracks.
---

# Floating Swarm — Multi-Agent Worktree Orchestration

Scale Floating Loops horizontally when a `prd.json` has independent stories (`dependsOn` DAG waves) or when a broad epic benefits from concurrent workers plus an independent **Adversarial Verifier**.

## Choose Your Swarm Primitive

| Situation | Primitive | Why |
| :--- | :--- | :--- |
| Deterministic `prd.json` with clear `dependsOn` waves & per-story checks | **Path 1: `run_workflow` DAG Swarm** | Scripted Python waves, isolated `workspace="share"` worktrees, strict JSON Schema outputs, automatic adversarial verify -> merge -> post-merge gate. |
| Exploratory, cross-cutting architecture needing live peer debate & human UI supervision | **Path 2: Live `/swarm` Coordinator** | Shared append-only board, zero-token idle workers, dynamic task steering, live Swarm UI. |

---

## Path 1 — Deterministic Worktree DAG Swarm (`run_workflow`)

### Prerequisites
1. Ensure `prd.json` exists and passes DAG validation:
   ```bash
   python3 <LOOP_ROOT>/scripts/loop-ctl.py validate-prd prd.json
   python3 <LOOP_ROOT>/scripts/loop-ctl.py next-wave prd.json
   ```
2. Ensure the git working tree is clean (`git status --short`).
3. Verify each story in the same wave touches non-overlapping files (check `filesHint`) to minimize merge conflicts.

### Step 1: Dry-Run the Wave Plan
Invoke `run_workflow` pointing at `<LOOP_ROOT>/scripts/workflows/floating_swarm.py` with `"dry_run": true` first to preview the ready wave and blocked stories:

```json
{
  "prd_path": "prd.json",
  "dry_run": true
}
```

### Step 2: Execute the Parallel Worktree Swarm
Call `run_workflow`:
- `ScriptPath`: `<absolute path to LOOP_ROOT>/scripts/workflows/floating_swarm.py`
- `Description`: `"Run Floating Swarm parallel DAG waves on prd.json"`
- `Args`:
  ```json
  {
    "prd_path": "prd.json",
    "max_waves": 8,
    "max_concurrency": 4,
    "max_retries_per_story": 2,
    "worker_model": "pro",
    "verifier_model": "pro",
    "escalate_model": "pro",
    "dry_run": false
  }
  ```

### What `floating_swarm.py` Does Per Wave
1. **Wave Planning**: Computes all unblocked stories (`passes: false`, all `dependsOn` satisfied), capped at `max_concurrency`.
2. **Parallel Isolated Worktrees (`workspace="share"`)**: Spawns one Implementer agent per story on a unique worker branch (`floating-loop/worker-<id>-w<wave>-a<attempt>`). Workers never edit `prd.json` or `progress.txt`.
3. **Adversarial Verifier Gate (`model="pro"`)**: Spawns an independent Verifier per worker branch to audit the exact `git diff <base>..<worker_branch>`, check every acceptance criterion, and reject skipped/weakened tests or incomplete logic.
4. **Sequential Integration & Backpressure**:
   - Approved branches merge via `git merge --no-ff` onto `prd.json`'s `branchName`.
   - Runs `verifyCommand` post-merge; failures revert the merge (`git reset --hard`) and queue the story for retry with failure feedback + `escalate_model`.
   - Passing merges update `prd.json` (`passes: true`), append learnings to `progress.txt`, commit cleanly, and prune the worker branch.

---

## Path 2 — Live `/swarm` Coordinator-Worker-Verifier Playbook

Use this playbook only when the user has explicitly initiated a `/swarm` session (never call the `swarm` tool unprompted outside `/swarm`):

1. **Stay a Non-Coding Coordinator**: Keep the coordinator conversation clean—never write feature code directly. Own the `prd.json` wave plan, progress tracking, and final merges.
2. **Shard Independent Tracks with `workspace: "share"`**:
   - Spawn workers by story/track (e.g., `name: "story"`, `tasks: ["US-002: ...", "US-003: ..."]`, `workspace: "share"`).
   - Route implementation workers and `@verifier` to frontier coding models (e.g., `gemini-4-neon-high` / `gemini-4-neon-xhigh` or `claude-opus-5-5-high` via `agy models`, or `pro` tier). Reserve lighter tiers only for pure read-only scouting if needed.
   - Instruct each worker to commit to its own branch and post `@verifier` + branch name + checks run when finished.
3. **Always Spawn an Independent `@verifier`**:
   - Spawn `name: "verifier"` (`workspace: "inherit"`, high-reasoning model) whose sole job is adversarial diff review (`git diff`), running tests, probing edge cases, and replying `APPROVED` or `CHANGES_REQUESTED` with concrete findings.
4. **Land Work Before Killing Workers**:
   - Merge verified worker branches onto the feature branch, re-run `verifyCommand`, update `prd.json` & `progress.txt`, and only then `kill` finished workers (killing permanently deletes a worker's `share` workspace).
5. **Leverage Zero-Token Idle Hygiene**:
   - Let idle workers sleep (`waiting` costs zero tokens); only wake them via `@mention` when new feedback or a new wave target is ready.
