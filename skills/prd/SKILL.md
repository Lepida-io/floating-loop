---
name: prd
description: >-
  Generate a structured Product Requirements Document (PRD) with small,
  dependency-tagged, objectively verifiable user stories sized for autonomous
  Floating Loop and Floating Swarm execution. Use when planning a feature,
  starting a new project, or asked to write a PRD or spec.
---

# PRD Generator (Floating Loop)

Author unambiguous PRDs structured into right-sized, dependency-aware user stories that an autonomous Antigravity Floating Loop (or parallel Floating Swarm) can execute and verify without human babysitting.

**Important**: Do **NOT** start implementing code during this workflow—produce only the PRD document.

---

## Step 1: Clarify Requirements

Before writing the PRD, ask **3–5 concise clarifying questions** covering only genuine ambiguities (use Antigravity's `ask_question` tool when available, or numbered questions with lettered options `A/B/C/D`):

- **Problem & Goal**: What user or engineering problem does this solve?
- **Core Capabilities**: What exact inputs, behaviors, and outputs are required?
- **Non-Goals / Scope**: What is explicitly out of scope?
- **Verification Gate**: What automated check(s) prove correctness (typecheck, test suite, benchmark, browser check)?

---

## Step 2: Author the Structured PRD

Write a Markdown PRD containing these sections:

1. **Introduction / Overview** — Problem statement and high-level summary.
2. **Goals** — Concrete, measurable objectives.
3. **User Stories (`US-001`, `US-002`, ...)** — Each story MUST be:
   - **Single-iteration sized**: Describable in 2–3 sentences; completable within one clean context window. Split broad stories ("Build auth", "Build dashboard") into schema -> backend/logic -> UI -> integration.
   - **Dependency-explicit**: List `Depends on: none` or `Depends on: US-001` so independent stories can later run in parallel worktree waves via `floating-swarm`.
   - **Scope-hinted**: Mention expected files/directories where obvious to minimize parallel merge conflicts.
   - **Objectively verifiable**: Every acceptance criterion must be checkable by a command, test, or browser inspection—never vague ("works well", "good UX", "handles edge cases").

   **Story Format**:
   ```markdown
   ### US-001: [Short Descriptive Title]
   - **Description:** As a [user/developer], I want [capability] so that [benefit].
   - **Depends on:** none (or `US-001, US-002`)
   - **Hat:** `architect` | `implementer` | `ui` | `tester` | `refactorer`
   - **Files Hint:** `src/db/`, `src/types/`
   - **Acceptance Criteria:**
     - [ ] Specific, objective behavioral requirement
     - [ ] Unit/integration test covering happy path and invalid input
     - [ ] Typecheck and test suite pass
     - [ ] *(UI stories only)* Verify in browser using Antigravity browser tools
   ```

4. **Functional Requirements (`FR-1`, `FR-2`, ...)** — Numbered, unambiguous specifications.
5. **Non-Goals (Out of Scope)** — Explicit boundaries to prevent scope creep during autonomous loops.
6. **Technical & Verification Considerations** — Project verification command (`verifyCommand`), architectural constraints, existing modules to reuse, and any performance/security invariants.
7. **Success Metrics** — Objective outcomes.

---

## Step 3: Save & Hand Off

- Save the PRD to `tasks/prd-[feature-name].md` (kebab-case).
- Review checklist before finishing:
  - [ ] Clarified ambiguities with the user first.
  - [ ] Every story fits a single fresh-context iteration.
  - [ ] Dependencies form an acyclic DAG (schema/types -> logic -> UI -> aggregation).
  - [ ] Every acceptance criterion is objectively verifiable; UI stories specify Antigravity browser verification.
  - [ ] Named the concrete verification command(s) the loop harness should enforce.
- Offer to run the `prd-to-loop` skill next to convert `tasks/prd-[feature-name].md` into a validated `prd.json`.
