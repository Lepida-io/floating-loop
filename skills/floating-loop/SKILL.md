---
name: floating-loop
description: >-
  Start, monitor, cancel, or recover an autonomous Floating Loop (Antigravity's
  next-gen Ralph Loop fork) across in-session Stop-hook loops, external
  fresh-context agy CLI loops (PRD and Autoresearch keep-or-revert), parallel
  worktree DAG workflows, and live /swarm playbooks. Use when the user asks to
  run a floating loop, iterate autonomously until tests pass, optimize a metric
  in a keep-or-revert loop, check loop status, reset a tripped circuit breaker,
  or cancel an active loop.
---

# Floating Loop — Autonomous Execution Controller

Run self-correcting, backpressure-gated autonomous loops natively in **Google Antigravity (`agy`)**.

## 1. Choose the Right Execution Topology

Pick the simplest mode that matches the task's horizon and parallelism:

| Need | Mode | Engine |
| :--- | :--- | :--- |
| Tight interactive polish, fixing tests/lint, `<= 10` iterations | **Mode A: In-Session `Stop` Hook** | `hooks.json` -> `scripts/stop-hook.py` |
| Multi-story `prd.json` backlog, overnight run, zero context rot | **Mode B: External Fresh-Context CLI** | `scripts/floating-loop.sh --mode prd` |
| Metric optimization (latency, bundle size, score) with auto-revert | **Mode B2: Autoresearch Loop** | `scripts/floating-loop.sh --mode autoresearch` |
| Independent parallelizable stories in `prd.json` (`dependsOn` DAG) | **Mode C: `run_workflow` Worktree DAG** | See skill `floating-swarm` (`scripts/workflows/floating_swarm.py`) |
| Exploratory, cross-cutting epic needing live peer debate & UI board | **Mode D: Live `/swarm` Coordinator** | See skill `floating-swarm` (`/swarm` playbook) |

> **Script location**: Resolve `LOOP_ROOT` as the directory containing this plugin (two levels up from this `SKILL.md`: `../../scripts/`).

---

## 2. Mode A — In-Session `Stop` Hook Loop

The `Stop` hook intercepts normal turn completion (`NO_TOOL_CALL`), inspects Antigravity's `transcript.jsonl` (with `transcript_full.jsonl` fallback), enforces Dual-Gate `--verify-cmd`, monitors context tokens, runs a 3-state circuit breaker, and re-enters the loop via `{"decision": "continue", "reason": "..."}`.

### Start an In-Session Loop

Always pass your current **Conversation ID** (from `<user_information>`) so the loop is strictly isolated to this session:

```bash
python3 <LOOP_ROOT>/scripts/loop-ctl.py start \
  --cwd "<workspace_path>" \
  --conversation-id "<Conversation ID>" \
  --max-iterations 15 \
  --completion-promise "DONE" \
  --verify-cmd "npm test && npm run typecheck" \
  --cb-threshold 3 \
  --max-context-tokens 180000 \
  --prompt "<clear task prompt>"
```

After running `start`, begin working on the task immediately. When you complete all work and verify `--verify-cmd` passes, output:

```text
<promise>DONE</promise>
```

**Strict rule**: Never emit `<promise>...</promise>` prematurely. If `--verify-cmd` fails, the Stop hook rejects the promise and feeds the failure output back as mechanical backpressure.

### Monitor, Cancel, or Recover Circuit Breaker

```bash
# Check status (in-session loops + optional prd.json progress)
python3 <LOOP_ROOT>/scripts/loop-ctl.py status --cwd "<workspace_path>" --prd prd.json

# Cancel active loop for this session
python3 <LOOP_ROOT>/scripts/loop-ctl.py cancel --cwd "<workspace_path>" --conversation-id "<Conversation ID>"

# Reset a tripped circuit breaker (OPEN -> CLOSED) or paused context-limit session
python3 <LOOP_ROOT>/scripts/loop-ctl.py reset-cb --cwd "<workspace_path>" --conversation-id "<Conversation ID>"
```

### Active-Loop Safety Guardrail (`PreToolUse`)

While a loop is `active` for your `conversationId`, `scripts/guard-hook.py` automatically blocks direct edits/deletes on `.floating-loop/sessions/`, catastrophic `rm -rf` on `/` or `~`, `git push --force`, and `git reset --hard` (unless `--allow-reset` was set). Outside active loops, it is a silent no-op.

---

## 3. Mode B — External Fresh-Context CLI Loop (`agy --print`)

Each iteration launches a fresh `agy --dangerously-skip-permissions -p="..."` process so context never rots. Memory lives solely in `prd.json`, `progress.txt`, `AGENTS.md`, and git commits.

### Story-Driven PRD Execution

1. Validate the PRD first:
   ```bash
   python3 <LOOP_ROOT>/scripts/loop-ctl.py validate-prd prd.json
   ```
2. Preview with `--dry-run` (recommended):
   ```bash
   bash <LOOP_ROOT>/scripts/floating-loop.sh --mode prd --prd prd.json --dry-run
   ```
3. Launch the loop (defaults to `gemini-4-neon-high`, automatically escalating to `gemini-4-neon-xhigh` on retry, with Dual-Gate verification):
   ```bash
   bash <LOOP_ROOT>/scripts/floating-loop.sh \
     --mode prd \
     --prd prd.json \
     --max-iterations 20 \
     --model gemini-4-neon-high \
     --escalate-model gemini-4-neon-xhigh \
     --cb-threshold 3
   ```
   *(Pass `--model claude-opus-5-5-high` or `--model gemini-4-carbon-high` to override, or `--model default` to inherit your ambient CLI setting.)*

### Raw Prompt Fresh-Context Mode

```bash
bash <LOOP_ROOT>/scripts/floating-loop.sh \
  --mode prompt \
  --prompt-file task-prompt.md \
  --completion-promise "ALL_GREEN" \
  --verify-cmd "pytest -q" \
  --max-iterations 10
```

### Autoresearch Keep-or-Revert Mode

Optimize a quantitative metric (latency, test runtime, bundle bytes, eval accuracy) safely. Regressions or failed verifications are automatically reverted via `git reset --hard` and logged in `results.tsv`:

```bash
bash <LOOP_ROOT>/scripts/floating-loop.sh \
  --mode autoresearch \
  --goal "Reduce parser p95 latency without breaking unit tests" \
  --metric-cmd "python3 bench/measure_p95.py" \
  --direction minimize \
  --verify-cmd "pytest -q" \
  --max-iterations 15 \
  --cb-threshold 4
```

---

## 4. Mid-Run Takeover from Upstream Ralph Loops

Take over an already-running `claude-ralph-loop` (`.claude/ralph-loop.local.md`), `gemini-cli-extensions/ralph` (`.gemini/ralph-loop.local.md`), or `snarktank/ralph` (`prd.json` + `progress.txt`) mid-run without losing progress:

```bash
# Auto-detect and take over (in-session or prd.json)
python3 <LOOP_ROOT>/scripts/loop-ctl.py takeover \
  --cwd "<workspace_path>" \
  --conversation-id "<Conversation ID>" \
  --verify-cmd "npm run typecheck && npm test"
```

- **In-session takeover**: Imports exact `iteration`, `max_iterations`, `completion_promise`, and prompt body into `.floating-loop/sessions/`, attaches Dual-Gate `--verify-cmd` + circuit breaker, and archives/removes `.claude/ralph-loop.local.md` so the old hook cannot collide.
- **`snarktank/ralph` PRD takeover**: Preserves `progress.txt` and `ralph/<feature>` branch, surfaces any interrupted uncommitted edits (`git status`) and legacy `CLAUDE.md` files, and optionally converts implicit priority order into explicit `dependsOn` edges (`--add-sequential-deps`).

---

## 5. Handoff & Context Hygiene

If an in-session loop pauses because it reached `--max-context-tokens`:
1. Summarize durable discoveries in `progress.txt` under `## Codebase Patterns`.
2. Either recommend `/compact` to the user and run `loop-ctl.py reset-cb`, **or** hand off the remaining work cleanly to `scripts/floating-loop.sh` (or `floating-swarm`) for fresh-context execution.
