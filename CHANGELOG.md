# Changelog

All notable changes to `floating-loop` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-26

### Added
- Native Google Antigravity (`agy`) plugin layout (`plugin.json`, `hooks.json`, `rules/AGENTS.md`, `skills/`, `scripts/`, `assets/logo.svg`).
- **Four execution topologies**:
  - **Mode A**: In-session `Stop` hook loop (`scripts/stop-hook.py`) with `conversationId` isolation, Antigravity `transcript.jsonl` + `transcript_full.jsonl` parsing, Dual-Gate `verifyCommand` backpressure, 3-state circuit breaker, and context-rot token ceiling.
  - **Mode B**: External fresh-context CLI runner (`scripts/floating-loop.sh`) supporting `prd`, `prompt`, and Karpathy-inspired `autoresearch` keep-or-revert modes with automatic model/effort escalation on retry.
  - **Mode C**: Parallel worktree DAG swarm (`scripts/workflows/floating_swarm.py`) using Antigravity `run_workflow` (`workspace="share"`), Kahn DAG wave scheduling, an independent `pro`-tier Adversarial Verifier, and post-merge backpressure rollback.
  - **Mode D**: Live `/swarm` Coordinator-Worker-Verifier playbook (`skills/floating-swarm/SKILL.md`).
- Active-loop `PreToolUse` safety guardrail (`scripts/guard-hook.py`) blocking catastrophic `rm`, force-pushes, unauthorized `git reset --hard`, and `.floating-loop/sessions/` tampering.
- Four bundled skills: `floating-loop`, `floating-swarm`, `prd`, and `prd-to-loop`.
- Mid-run takeover (`scripts/loop-ctl.py takeover`) from upstream `claude-ralph-loop` (`.claude/ralph-loop.local.md`) and `snarktank/ralph` (`prd.json` + `progress.txt`).
- Zero-dependency stdlib Python 3.8+ core (`scripts/loop_core.py`) and JSON Schema (`schemas/prd.schema.json`).
- Offline unit, hook, swarm workflow, and mocked CLI integration test suite (`tests/run_all.sh`).
