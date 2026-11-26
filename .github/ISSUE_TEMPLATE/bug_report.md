---
name: Bug report
about: Report a bug in floating-loop hooks, CLI runner, swarm workflow, or skills
title: "[bug] "
labels: bug
---

## Describe the bug
A clear and concise description of what went wrong.

## Execution mode
- [ ] Mode A — In-session `Stop` hook
- [ ] Mode B — External `floating-loop.sh` (`prd` / `prompt` / `autoresearch`)
- [ ] Mode C — Parallel worktree DAG swarm (`floating_swarm.py`)
- [ ] Mode D — Live `/swarm` playbook
- [ ] Upstream Ralph takeover (`loop-ctl.py takeover`)
- [ ] Plugin install / validation (`agy plugin install` / `validate`)

## Steps to reproduce
1. ...
2. ...
3. ...

## Expected behavior
What you expected to happen.

## Environment
- OS:
- `agy --version`:
- `python3 --version`:
- `floating-loop` version / commit:

## Logs / output
Paste any relevant output from `loop-ctl.py status`, `tests/run_all.sh`, or hook logs.
