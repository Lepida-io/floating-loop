<p align="center">
  <img src="assets/logo.svg" width="96" height="96" alt="floating-loop logo" />
</p>

<h1 align="center">floating-loop</h1>

<p align="center">
  <strong>Autonomous Ralph-inspired iterative loops, keep-or-revert autoresearch, and parallel multi-agent worktree swarms built natively for Google Antigravity (<code>agy</code>)</strong><br />
  <em>Because in Antigravity, loops float.</em>
</p>

<p align="center">
  <a href="https://github.com/Artitus/floating-loop/actions/workflows/ci.yml"><img src="https://github.com/Artitus/floating-loop/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/Antigravity-Native%20Plugin-38BDF8.svg" alt="Google Antigravity Native Plugin" />
  <img src="https://img.shields.io/badge/dependencies-zero%20(Python%203.8%2B%20stdlib)-818CF8.svg" alt="Zero External Dependencies" />
</p>

---

## What Is Floating Loop?

`floating-loop` evolves the classic [Ralph Wiggum autonomous loop](https://ghuntley.com/ralph/) (`snarktank/ralph` + `claude-ralph-loop`) for Google Antigravity's native customization and orchestration primitives — adding mechanical verification, circuit breakers, context-rot telemetry, keep-or-revert metric optimization, and parallel Git worktree DAG swarms with **zero external dependencies**.

### Why Floating Loop vs. Classic Ralph Loops?

| Capability | Classic Ralph Loop | `floating-loop` |
| --- | --- | --- |
| **Completion Integrity** | Trusts `<promise>` text alone | **Dual-Gate Verification**: independently runs `verifyCommand` and rejects premature promises or fake `passes: true` |
| **Stall & Error Protection** | Loops until max iterations | **3-State Circuit Breaker** (`CLOSED -> HALF_OPEN -> OPEN`) on zero-diff stalls or repeated identical errors |
| **Context-Rot Defense** | None (in-session degrades silently) | Reads `input_tokens + cache_read_tokens` from Antigravity `transcript.jsonl` and pauses cleanly at ceiling |
| **Parallel Execution** | Sequential single-agent only | **Parallel Worktree DAG Swarm** (`dependsOn` waves in `workspace="share"` worktrees + `pro`-tier Adversarial Verifier) |
| **Optimization Loops** | Feature tasks only | **Autoresearch Keep-or-Revert Mode** (`results.tsv` + automatic `git reset --hard` on metric regression) |
| **Retry Intelligence** | Same model every attempt | **Role Hats & Model/Effort Cascade**: steps up model/effort automatically when verification fails |
| **Active-Loop Safety** | Relies on prompt instructions | **`PreToolUse` Guardrail**: blocks `rm -rf /`, `git push --force`, unauthorized `git reset --hard`, and state tampering |
| **Upstream Migration** | Start from scratch | **Mid-Run Takeover**: resume active `claude-ralph-loop` or `snarktank/ralph` runs without losing progress |

---

## Which Mode Should I Use?

| Mode | Best For | Context | Entrypoint |
| --- | --- | --- | --- |
| **Mode A — In-Session `Stop` Hook** | Fast interactive bug-fixing & test polishing | Shared session (with token ceiling) | `hooks.json` + `scripts/loop-ctl.py start` |
| **Mode B — Fresh-Context CLI (`prd` / `prompt`)** | Multi-story overnight feature builds | Fresh `agy -p` per iteration (zero rot) | `./scripts/floating-loop.sh --mode prd` |
| **Mode B — Autoresearch** | Optimizing latency, bundle size, score, or benchmarks | Fresh `agy -p` + keep-or-revert | `./scripts/floating-loop.sh --mode autoresearch` |
| **Mode C — Worktree DAG Swarm** | Large PRDs with independent parallelizable stories | Isolated `workspace="share"` worktrees | `floating-swarm` skill -> `floating_swarm.py` |
| **Mode D — Live `/swarm` Playbook** | Exploratory, cross-cutting epics needing coordination | Shared Antigravity `/swarm` board | `floating-swarm` skill |
| **Takeover** | Migrating an in-progress `claude-ralph-loop` or `snarktank/ralph` run | Preserves state, branch & `progress.txt` | `scripts/loop-ctl.py takeover` |

---

## Installation

### Prerequisites
- [Google Antigravity CLI (`agy`)](https://antigravity.google) installed and authenticated
- Python `3.8+` (stdlib only — no `pip install`, `jq`, or Node required)
- `bash` 3.2+ and `git`

### Install from GitHub (Recommended)

```bash
agy plugin install https://github.com/Artitus/floating-loop
```

### Install from the Antigravity Plugin Marketplace

Open the **Antigravity App**, go to **Customizations → Marketplace**, search for **Floating Loop**, and click **Add**.

### Local Development / Clone

```bash
git clone https://github.com/Artitus/floating-loop.git
cd floating-loop
agy plugin validate . --strict
agy plugin install .
```

Verify the plugin is installed and enabled:

```bash
agy plugin list
```

---

## Quickstart

### 1. Author a PRD & Convert to `prd.json`
Inside `agy`, ask Antigravity to use the `prd` skill to plan your feature (`tasks/prd-<feature>.md`), then run `prd-to-loop` to produce a validated, dependency-tagged `prd.json`.

### 2. In-Session `Stop` Hook Loop (Mode A)
```bash
python3 scripts/loop-ctl.py start \
  --cwd "$(pwd)" \
  --conversation-id "<your-conversation-id>" \
  --max-iterations 15 \
  --completion-promise "DONE" \
  --verify-cmd "pytest -q" \
  --prompt "Fix all failing tests in tests/auth/ without skipping any assertions."
```
Monitor, reset, or stop at any time:
```bash
python3 scripts/loop-ctl.py status --cwd "$(pwd)"
python3 scripts/loop-ctl.py cancel --cwd "$(pwd)"
python3 scripts/loop-ctl.py reset-cb --cwd "$(pwd)"
```

### 3. External Fresh-Context CLI Loop (Mode B — Zero Context Rot)
```bash
# Preview assembled prompt & command without side effects
./scripts/floating-loop.sh --mode prd --prd examples/prd.json.example --dry-run

# Run overnight PRD loop (auto-escalates model/effort on verification failure)
./scripts/floating-loop.sh \
  --mode prd \
  --prd prd.json \
  --max-iterations 20 \
  --cb-threshold 3
```

### 4. Autoresearch Keep-or-Revert Loop (Metric Optimization)
```bash
./scripts/floating-loop.sh \
  --mode autoresearch \
  --goal "Minimize JSON parser p95 latency" \
  --metric-cmd "python3 bench/p95.py" \
  --direction minimize \
  --verify-cmd "pytest -q" \
  --max-iterations 15
```
Improvements are kept and logged to `results.tsv`; regressions or broken tests are automatically reverted via `git reset --hard`.

### 5. Mid-Run Takeover from Upstream Ralph Loops
Resume an in-progress `claude-ralph-loop` (`.claude/ralph-loop.local.md`) or `snarktank/ralph` (`prd.json` + `progress.txt`) run directly in Antigravity:
```bash
python3 scripts/loop-ctl.py takeover \
  --cwd "$(pwd)" \
  --verify-cmd "npm run typecheck && npm test"
```
- Imports exact `iteration`, `max_iterations`, `completion_promise`, and prompt from `.claude/ralph-loop.local.md` (archiving the old file so hooks never collide).
- Preserves existing `progress.txt`, `ralph/<feature>` branch, and `passes: true` stories from `snarktank/ralph`, surfaces interrupted uncommitted edits and `CLAUDE.md` notes, and auto-serializes legacy `dependsOn`-less PRDs inside `floating_swarm.py`.

### 6. Parallel Worktree DAG Swarm (Mode C — `run_workflow`)
Ask Antigravity to activate the `floating-swarm` skill, which validates your `prd.json` waves and invokes `run_workflow` on `scripts/workflows/floating_swarm.py` — fanning out independent stories in `workspace="share"` worktrees and gating merges behind an independent `pro`-tier Adversarial Verifier.

---

## Architecture

```text
floating-loop/                          # Native Antigravity plugin root
├── plugin.json                         # Plugin manifest (Marketplace + `agy plugin install`)
├── hooks.json                          # Antigravity Stop + PreToolUse lifecycle hooks
├── rules/
│   └── AGENTS.md                       # Loop discipline, promise integrity, backpressure rules
├── skills/
│   ├── floating-loop/SKILL.md          # Core controller (Modes A, B, Autoresearch, status/cancel)
│   ├── floating-swarm/SKILL.md         # Multi-agent orchestration (run_workflow DAG + /swarm)
│   ├── prd/SKILL.md                    # Dependency-aware PRD authoring skill
│   └── prd-to-loop/SKILL.md            # PRD -> validated prd.json DAG converter skill
├── scripts/
│   ├── loop_core.py                    # Shared stdlib-only Python 3 core (zero external deps)
│   ├── stop-hook.py                    # Antigravity Stop hook (Dual-Gate + CB + context ceiling)
│   ├── guard-hook.py                   # Active-loop PreToolUse safety guard
│   ├── loop-ctl.py                     # CLI: start, status, cancel, reset-cb, takeover, waves
│   ├── floating-loop.sh                # External fresh-context agy -p runner (prd/prompt/autoresearch)
│   ├── prompts/                        # Iteration, autoresearch, and role-hat prompt templates
│   └── workflows/
│       └── floating_swarm.py           # Native `run_workflow` parallel worktree DAG + verifier
├── schemas/prd.schema.json             # JSON Schema for enhanced prd.json
├── examples/prd.json.example           # Multi-wave example PRD
├── tests/                              # Automated unit, hook, swarm, and CLI integration tests
└── .agents/skills.json                 # Local workspace pointer to root `skills/` (zero duplication)
```

---

## Safety & Security

- **Active-Loop `PreToolUse` Guardrail**: Zero-overhead passthrough outside active loops; during an active loop, blocks catastrophic `rm -rf`, `git push --force`, unauthorized `git reset --hard`, and direct `.floating-loop/sessions/` edits.
- **PRD Anti-Tampering**: Snapshots `prd.json` before every iteration and restores it if story IDs are removed, corrupted, or non-target stories are marked complete.
- **Bounded Execution**: Always configure `--max-iterations` and `--cb-threshold`. Run unattended `--dangerously-skip-permissions` loops (Mode B) only on dedicated feature branches or isolated sandboxes.

See [SECURITY.md](SECURITY.md) for full details and vulnerability reporting.

---

## Running Tests

Offline strict plugin validation, syntax checks, Python unit/hook/swarm tests, and mocked CLI integration tests:

```bash
bash tests/run_all.sh
```

Live end-to-end tests against the real `agy` CLI (Stop hook + Dual-Gate, external PRD loop, and Autoresearch keep-or-revert):

```bash
bash tests/test_e2e_live.sh --model gemini-3.8-flash-low
```

---

## Contributing & Changelog

- Contributions are welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) for architecture invariants and verification commands.
- See [CHANGELOG.md](CHANGELOG.md) for release history.

## Acknowledgments

Inspired by [Geoff Huntley's Ralph Wiggum technique](https://ghuntley.com/ralph/), `snarktank/ralph`, `claude-ralph-loop`, and Andrej Karpathy's autoresearch keep-or-revert workflow.

## License

Released under the [MIT License](LICENSE).
