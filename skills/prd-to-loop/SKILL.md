---
name: prd-to-loop
description: >-
  Convert a Markdown PRD into a schema-validated, dependency-ordered prd.json
  task list for Floating Loop and Floating Swarm autonomous execution. Use when
  the user asks to convert a PRD into loop format, create prd.json, split
  stories into waves, or prepare tasks for floating-loop.
---

# PRD to Floating Loop Converter

Transform a Markdown PRD (`tasks/prd-*.md` or inline spec) into a validated, dependency-aware `prd.json` ready for serial fresh-context execution (`floating-loop.sh`) or parallel worktree DAG execution (`floating_swarm.py`).

## Target Schema

Conforms to `schemas/prd.schema.json` (see `examples/prd.json.example` for a full multi-wave example):

```json
{
  "project": "ProjectName",
  "branchName": "floating-loop/feature-name-kebab",
  "description": "Concise feature summary",
  "verifyCommand": "npm run typecheck && npm test",
  "defaultEffort": "medium",
  "maxRetriesPerStory": 3,
  "userStories": [
    {
      "id": "US-001",
      "title": "Short descriptive title",
      "description": "As a [user], I want [feature] so that [benefit].",
      "acceptanceCriteria": [
        "Specific, objectively verifiable criterion",
        "Typecheck and tests pass"
      ],
      "priority": 1,
      "dependsOn": [],
      "hat": "architect",
      "filesHint": ["src/schema/"],
      "passes": false,
      "notes": ""
    }
  ]
}
```

## Conversion Rules

1. **Archive Previous Runs First**:
   If `prd.json` already exists in the workspace with a **different** `branchName`, archive the prior run before overwriting:
   ```bash
   python3 <LOOP_ROOT>/scripts/loop-ctl.py archive-check --cwd "<workspace>" --prd prd.json --progress progress.txt
   ```
2. **One Iteration Per Story**:
   Split any story that cannot be described in 2–3 sentences. Prefer 4–10 crisp stories over 2 giant ones.
3. **Explicit DAG Dependencies (`dependsOn`)**:
   - Order foundational stories (schemas, migrations, shared types) at `priority: 1` with `dependsOn: []`.
   - Mark independent downstream stories (e.g., API endpoint vs. UI card) with the same prerequisite `dependsOn: ["US-001"]` and non-overlapping `filesHint` so `floating-swarm` can execute them in parallel worktrees!
   - Never create cycles or forward dependencies.
4. **Mechanical Backpressure (`verifyCommand`)**:
   - Always inspect the workspace (`package.json`, `pyproject.toml`, `Makefile`, `Cargo.toml`, `go.mod`) to populate a real, working top-level `verifyCommand` (and optional per-story `verifyCommand`).
5. **Role Hats & Routing**:
   - Assign `hat` (`architect`, `implementer`, `ui`, `tester`, `refactorer`) to sharpen each iteration's system focus.
   - Optionally set `effort` (`medium` for routine work, `high` for complex integration) or `model` overrides where helpful.
6. **Objective Acceptance Criteria**:
   - Replace vague phrases ("works well", "good UX") with checkable facts.
   - Include `"Typecheck and tests pass"` (or project equivalent) on every story.
   - Include `"Verify in browser using Antigravity browser tools"` on every UI story.
7. **Always Start Clean**:
   - Every new story starts with `"passes": false` and `"notes": ""`.

## Mandatory Validation Gate

After writing `prd.json`, ALWAYS validate both schema and DAG wave structure:

```bash
python3 <LOOP_ROOT>/scripts/loop-ctl.py validate-prd prd.json --strict
python3 <LOOP_ROOT>/scripts/loop-ctl.py next-wave prd.json
```

Fix any reported errors, dependency cycles, or vague-criterion warnings before reporting completion. Then present the wave breakdown and offer to start either `floating-loop` (serial fresh-context / in-session) or `floating-swarm` (parallel worktree DAG).
