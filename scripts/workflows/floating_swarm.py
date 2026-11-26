"""Floating Loop — Antigravity `run_workflow` Parallel Worktree DAG Swarm.

Run via Antigravity's `run_workflow` tool:
    run_workflow(
        ScriptPath="/abs/path/to/scripts/workflows/floating_swarm.py",
        Description="Run Floating Loop parallel DAG swarm on prd.json",
        Args={
            "prd_path": "prd.json",
            "max_waves": 8,
            "max_concurrency": 4,
            "worker_model": "inherit",
            "verifier_model": "pro",
            "escalate_model": "pro",
            "dry_run": False,
        },
    )

Note: Antigravity `run_workflow` executes scripts in an isolated Python process
where top-level `await`, `log`, `phase`, `agent`, `parallel`, `WORKSPACES`, and
`args` are injected as globals (and local modules are not importable).
"""

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Structured JSON Schemas enforced on every worker & adversarial verifier
# ---------------------------------------------------------------------------

IMPLEMENTER_SCHEMA = {
    "type": "object",
    "required": ["storyId", "branch", "commitSha", "summary", "filesChanged", "checksRun", "selfVerified"],
    "properties": {
        "storyId": {"type": "string"},
        "branch": {"type": "string"},
        "commitSha": {"type": "string"},
        "summary": {"type": "string"},
        "filesChanged": {"type": "array", "items": {"type": "string"}},
        "checksRun": {"type": "array", "items": {"type": "string"}},
        "selfVerified": {"type": "boolean"},
        "learnings": {"type": "string"},
    },
}

VERIFIER_SCHEMA = {
    "type": "object",
    "required": ["storyId", "approved", "summary", "findings", "missingCriteria"],
    "properties": {
        "storyId": {"type": "string"},
        "approved": {"type": "boolean"},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "missingCriteria": {"type": "array", "items": {"type": "string"}},
    },
}

VALID_WORKFLOW_MODELS = {"inherit", "flash_lite", "flash", "pro"}


def _git(cwd: Path, git_args: list, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *git_args],
        capture_output=True,
        text=True,
        check=check,
    )


def _normalize_model(value: str, fallback: str = "inherit") -> str:
    if value in VALID_WORKFLOW_MODELS:
        return value
    return fallback if fallback in VALID_WORKFLOW_MODELS else "inherit"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-") or "story"


def _compute_ready_wave(prd: dict, exhausted_ids: set) -> tuple:
    stories = prd.get("userStories") or []
    passed_ids = {s["id"] for s in stories if s.get("passes") is True}
    ready = []
    blocked = []
    for idx, s in enumerate(stories):
        sid = s["id"]
        if s.get("passes") is True or sid in exhausted_ids:
            continue
        deps = s.get("dependsOn") or []
        missing = [d for d in deps if d not in passed_ids]
        if not missing:
            ready.append((int(s.get("priority", 9999)), idx, s))
        else:
            blocked.append((sid, missing))
    ready.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ready], blocked, len(passed_ids), len(stories)


def _ensure_feature_branch(cwd: Path, branch_name: str) -> None:
    cur = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if cur == branch_name:
        return
    exists = _git(cwd, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"]).returncode == 0
    if exists:
        _git(cwd, ["checkout", branch_name], check=True)
    else:
        _git(cwd, ["checkout", "-b", branch_name], check=True)


def _read_patterns_header(progress_path: Path) -> str:
    if not progress_path.is_file():
        return ""
    text = progress_path.read_text(encoding="utf-8", errors="replace")
    return text[:2500]


def _append_progress(progress_path: Path, story_id: str, title: str, summary: str, learnings: str) -> None:
    if not progress_path.is_file():
        progress_path.write_text(
            "# Floating Loop Progress Log\n\n## Codebase Patterns\n\n---\n",
            encoding="utf-8",
        )
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (
        f"\n## {ts} - {story_id}: {title}\n"
        f"- Summary: {summary}\n"
        f"- Learnings: {learnings or 'None noted'}\n"
        "---\n"
    )
    with progress_path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def _build_implementer_prompt(
    story: dict,
    feature_branch: str,
    worker_branch: str,
    base_sha: str,
    verify_cmd: str,
    patterns_excerpt: str,
    prior_feedback: str,
) -> str:
    criteria_lines = "\n".join(f"- {c}" for c in story.get("acceptanceCriteria", []))
    files_hint = ", ".join(story.get("filesHint") or []) or "(none specified)"
    hat = story.get("hat", "implementer")
    feedback_block = ""
    if prior_feedback:
        feedback_block = (
            "\n## Prior Adversarial Verifier / Merge Feedback (MUST ADDRESS)\n"
            f"{prior_feedback}\n"
        )
    return (
        f"Implement story {story['id']} ({story['title']}) in isolated worktree branch `{worker_branch}`.\n\n"
        f"## Role / Hat: {hat}\n"
        f"You are working inside an isolated `workspace='share'` copy of the repository.\n"
        f"Base feature branch: `{feature_branch}` (commit `{base_sha[:12]}`).\n\n"
        f"## Story Details\n"
        f"- ID: `{story['id']}`\n"
        f"- Title: {story['title']}\n"
        f"- Description: {story['description']}\n"
        f"- Suggested files scope: {files_hint}\n"
        f"- Acceptance Criteria:\n{criteria_lines}\n"
        f"{feedback_block}\n"
        f"## Codebase Patterns (from progress.txt)\n{patterns_excerpt or '(none yet)'}\n\n"
        "## Strict Execution Steps\n"
        f"1. Create and check out your dedicated worker branch from `{base_sha}`:\n"
        f"   `git checkout -B {worker_branch} {base_sha}`\n"
        "2. Implement ONLY this single story cleanly. Do NOT edit `prd.json` or `progress.txt` (the integrator updates those after verification and merge).\n"
        f"3. Run quality checks{f': `{verify_cmd}`' if verify_cmd else ' (typecheck/lint/tests)'}. Do NOT skip or weaken tests.\n"
        f"4. Commit all changes on `{worker_branch}` with message `feat: {story['id']} - {story['title']}` and confirm `git status` is clean.\n"
        "5. Call `finish` returning the exact JSON schema with `branch`, `commitSha`, `filesChanged`, `checksRun`, and `selfVerified`.\n"
    )


def _build_verifier_prompt(
    story: dict,
    impl_result: dict,
    base_sha: str,
    verify_cmd: str,
) -> str:
    criteria_lines = "\n".join(f"- {c}" for c in story.get("acceptanceCriteria", []))
    return (
        f"Adversarially verify worker branch `{impl_result['branch']}` for story {story['id']}.\n\n"
        "## Adversarial Verifier Mandate\n"
        "Your job is to FIND defects, unfulfilled acceptance criteria, edge-case bugs, skipped/weakened tests, or out-of-scope edits.\n"
        "Do NOT trust the implementer's summary without reading the real diff!\n\n"
        f"## Target Story: {story['id']} — {story['title']}\n"
        f"Description: {story['description']}\n"
        f"Acceptance Criteria:\n{criteria_lines}\n\n"
        "## Implementer Claim\n"
        f"```json\n{json.dumps(impl_result, indent=2)}\n```\n\n"
        "## Verification Procedure (Read-Only Inspection)\n"
        f"1. Inspect the exact diff against base `{base_sha}`:\n"
        f"   `git diff --stat {base_sha}..{impl_result['branch']}` and `git diff {base_sha}..{impl_result['branch']}`\n"
        "2. Confirm every single acceptance criterion is genuinely satisfied in the diff.\n"
        "3. Confirm no tests were deleted, skipped, or weakened, and no unrelated files or `prd.json` were modified.\n"
        f"4. Verify tests/checks cover the new behavior{f' (project verifyCommand: `{verify_cmd}`)' if verify_cmd else ''}.\n"
        "5. Do NOT modify files in the workspace. Call `finish` with `approved: true` ONLY if zero blocking issues exist; "
        "otherwise set `approved: false` and list exact actionable `findings` and `missingCriteria`.\n"
    )


async def run_floating_swarm() -> dict:
    if args.get("cwd"):
        cwd = Path(str(args["cwd"])).resolve()
    elif WORKSPACES:
        cwd = Path(WORKSPACES[0]).resolve()
    else:
        cwd = Path.cwd().resolve()
    prd_path = Path(args.get("prd_path", "prd.json"))
    if not prd_path.is_absolute():
        prd_path = cwd / prd_path
    progress_path = cwd / "progress.txt"

    if not prd_path.is_file():
        raise FileNotFoundError(f"PRD file not found: {prd_path}")

    prd = json.loads(prd_path.read_text(encoding="utf-8"))
    feature_branch = prd["branchName"]
    default_verify = prd.get("verifyCommand", "")
    max_waves = int(args.get("max_waves", 10))
    max_concurrency = max(1, int(args.get("max_concurrency", 4)))
    allow_implicit_parallel = bool(args.get("allow_implicit_parallel", False))
    max_retries = int(args.get("max_retries_per_story", prd.get("maxRetriesPerStory", 2)))
    worker_model_default = _normalize_model(str(args.get("worker_model", "pro")), "pro")
    verifier_model = _normalize_model(str(args.get("verifier_model", "pro")), "pro")
    escalate_model = _normalize_model(str(args.get("escalate_model", "pro")), "pro")
    dry_run = bool(args.get("dry_run", False))

    # Legacy snarktank/ralph PRDs omit `dependsOn` and rely on `priority` for sequential order.
    # Auto-serialize to concurrency=1 unless stories explicitly declare `dependsOn` or opted in.
    stories_all = prd.get("userStories") or []
    has_explicit_deps = any("dependsOn" in s for s in stories_all)
    if not has_explicit_deps and max_concurrency > 1 and not allow_implicit_parallel:
        max_concurrency = 1
        log(
            "Legacy prd.json detected (no 'dependsOn' fields): serializing waves to concurrency=1 "
            "in priority order. Add 'dependsOn' or set allow_implicit_parallel=True to parallelize."
        )

    phase("Plan")
    if dry_run:
        ready, blocked, passed_cnt, total_cnt = _compute_ready_wave(prd, set())
        log(f"[DRY-RUN] Project={prd['project']} Branch={feature_branch} ({passed_cnt}/{total_cnt} passed)")
        log(f"[DRY-RUN] Ready wave ({len(ready)}): {[s['id'] for s in ready[:max_concurrency]]}")
        log(f"[DRY-RUN] Blocked ({len(blocked)}): {blocked}")
        return {
            "dryRun": True,
            "readyWave": [s["id"] for s in ready[:max_concurrency]],
            "blocked": blocked,
        }

    # Ensure clean git working tree before starting parallel worktree waves
    dirty = _git(cwd, ["status", "--porcelain", "--", ".", ":(exclude).floating-loop"]).stdout.strip()
    if dirty:
        raise RuntimeError(f"Working tree is dirty. Commit or stash changes before running floating_swarm:\n{dirty}")

    _ensure_feature_branch(cwd, feature_branch)
    log(f"Floating Swarm active on branch `{feature_branch}` (max_waves={max_waves}, concurrency={max_concurrency})")

    retry_counts = {}
    retry_feedback = {}
    exhausted_ids = set()
    merged_stories = []

    for wave_idx in range(1, max_waves + 1):
        prd = json.loads(prd_path.read_text(encoding="utf-8"))
        ready, blocked, passed_cnt, total_cnt = _compute_ready_wave(prd, exhausted_ids)

        if passed_cnt == total_cnt:
            log(f"All {total_cnt}/{total_cnt} stories verified and merged!")
            break
        if not ready:
            log(f"Stopping: no ready stories remaining (exhausted={sorted(exhausted_ids)}, blocked={blocked}).")
            break

        batch = ready[:max_concurrency]
        base_sha = _git(cwd, ["rev-parse", "HEAD"], check=True).stdout.strip()
        patterns_excerpt = _read_patterns_header(progress_path)

        # Phase A: Parallel isolated worktree implementation
        phase(f"Wave {wave_idx}: Implement ({', '.join(s['id'] for s in batch)})")
        log(f"Dispatching {len(batch)} parallel worker(s) from base {base_sha[:12]}")

        def _make_worker(story):
            sid = story["id"]
            attempt = retry_counts.get(sid, 0) + 1
            worker_branch = f"floating-loop/worker-{_slug(sid)}-w{wave_idx}-a{attempt}"
            story_verify = story.get("verifyCommand") or default_verify
            chosen_model = (
                escalate_model
                if attempt > 1
                else _normalize_model(str(story.get("model") or worker_model_default), worker_model_default)
            )
            prompt = _build_implementer_prompt(
                story=story,
                feature_branch=feature_branch,
                worker_branch=worker_branch,
                base_sha=base_sha,
                verify_cmd=story_verify,
                patterns_excerpt=patterns_excerpt,
                prior_feedback=retry_feedback.get(sid, ""),
            )
            return agent(
                prompt=prompt,
                schema=IMPLEMENTER_SCHEMA,
                role=f"Implement {sid} (attempt {attempt})",
                workspace="share",
                model=chosen_model,
            )

        impl_results = await parallel(batch, _make_worker)
        story_by_id = {s["id"]: s for s in batch}

        # Pre-verifier branch & anti-tampering gate: ensure worker branch exists,
        # actually has commits ahead of base_sha, and did not tamper with prd.json!
        prd_rel = prd_path.resolve().relative_to(cwd).as_posix()
        verifiable_results = []
        pre_rejected_verdicts = {}
        for res in impl_results:
            sid = res["storyId"]
            wbranch = res.get("branch", "")
            ref_ok = bool(wbranch) and _git(cwd, ["show-ref", "--verify", "--quiet", f"refs/heads/{wbranch}"]).returncode == 0
            if not ref_ok:
                pre_rejected_verdicts[sid] = {
                    "storyId": sid,
                    "approved": False,
                    "summary": f"Worker branch `{wbranch}` does not exist in repository.",
                    "findings": [f"Missing branch ref: {wbranch}"],
                    "missingCriteria": ["Commit changes to dedicated worker branch"],
                }
                continue
            ahead = _git(cwd, ["rev-list", "--count", f"{base_sha}..{wbranch}"]).stdout.strip()
            if not ahead.isdigit() or int(ahead) == 0:
                pre_rejected_verdicts[sid] = {
                    "storyId": sid,
                    "approved": False,
                    "summary": f"Worker branch `{wbranch}` has zero commits ahead of base {base_sha[:12]}.",
                    "findings": ["No commits produced on worker branch"],
                    "missingCriteria": ["Implement story and commit changes"],
                }
                continue
            changed_files = [
                line.strip()
                for line in _git(cwd, ["diff", "--name-only", f"{base_sha}..{wbranch}"]).stdout.splitlines()
                if line.strip()
            ]
            if prd_rel in changed_files:
                pre_rejected_verdicts[sid] = {
                    "storyId": sid,
                    "approved": False,
                    "summary": f"Worker modified `{prd_rel}` directly (forbidden in parallel swarm mode).",
                    "findings": [f"Forbidden modification to {prd_rel}"],
                    "missingCriteria": ["Do not modify prd.json inside worker branches"],
                }
                continue
            verifiable_results.append(res)

        # Phase B: Parallel Adversarial Verifier Gate
        phase(f"Wave {wave_idx}: Adversarial Verify ({len(verifiable_results)} branch(es))")

        def _make_verifier(res):
            story = story_by_id[res["storyId"]]
            story_verify = story.get("verifyCommand") or default_verify
            prompt = _build_verifier_prompt(story, res, base_sha, story_verify)
            return agent(
                prompt=prompt,
                schema=VERIFIER_SCHEMA,
                role=f"Adversarial Verifier {res['storyId']}",
                workspace="inherit",
                model=verifier_model,
            )

        verdicts = await parallel(verifiable_results, _make_verifier) if verifiable_results else []
        verdict_by_id = {**pre_rejected_verdicts, **{v["storyId"]: v for v in verdicts}}

        # Phase C: Sequential Integration, Post-Merge Backpressure & State Persistence
        phase(f"Wave {wave_idx}: Integrate & Gate")
        wave_merged = 0
        for res in impl_results:
            sid = res["storyId"]
            story = story_by_id[sid]
            verdict = verdict_by_id[sid]
            wbranch = res["branch"]

            if not verdict.get("approved"):
                findings = verdict.get("findings") or []
                missing = verdict.get("missingCriteria") or []
                msg = (
                    f"Adversarial verifier rejected {sid}: {verdict.get('summary')}\n"
                    f"Findings: {findings}\nMissing criteria: {missing}"
                )
                log(f"REJECTED {sid}: {verdict.get('summary')}")
                retry_counts[sid] = retry_counts.get(sid, 0) + 1
                retry_feedback[sid] = msg
                _git(cwd, ["branch", "-D", wbranch])
                if retry_counts[sid] > max_retries:
                    exhausted_ids.add(sid)
                    log(f"CIRCUIT BREAKER: {sid} exceeded max_retries ({max_retries}).")
                continue

            pre_merge_sha = _git(cwd, ["rev-parse", "HEAD"], check=True).stdout.strip()
            merge_proc = _git(cwd, ["merge", "--no-ff", "--no-edit", wbranch])
            if merge_proc.returncode != 0:
                _git(cwd, ["merge", "--abort"])
                log(f"MERGE CONFLICT on {sid} ({wbranch}) — queued for clean retry on updated base.")
                retry_counts[sid] = retry_counts.get(sid, 0) + 1
                retry_feedback[sid] = (
                    f"Branch `{wbranch}` had a merge conflict against `{feature_branch}`. "
                    "Re-implement cleanly on top of the updated base commit."
                )
                _git(cwd, ["branch", "-D", wbranch])
                if retry_counts[sid] > max_retries:
                    exhausted_ids.add(sid)
                continue

            story_verify = story.get("verifyCommand") or default_verify
            if story_verify:
                vproc = subprocess.run(story_verify, shell=True, cwd=str(cwd), capture_output=True, text=True)
                if vproc.returncode != 0:
                    tail = ((vproc.stdout or "") + "\n" + (vproc.stderr or ""))[-2500:].strip()
                    _git(cwd, ["reset", "--hard", pre_merge_sha], check=True)
                    log(f"POST-MERGE VERIFY FAILED for {sid} (exit {vproc.returncode}) — reverted merge.")
                    retry_counts[sid] = retry_counts.get(sid, 0) + 1
                    retry_feedback[sid] = f"Post-merge `{story_verify}` failed (exit {vproc.returncode}):\n{tail}"
                    _git(cwd, ["branch", "-D", wbranch])
                    if retry_counts[sid] > max_retries:
                        exhausted_ids.add(sid)
                    continue

            # Persist passes=true in prd.json + append progress.txt and commit
            prd_latest = json.loads(prd_path.read_text(encoding="utf-8"))
            for item in prd_latest["userStories"]:
                if item["id"] == sid:
                    item["passes"] = True
                    item["notes"] = f"Verified & merged via floating_swarm wave {wave_idx}"
                    break
            prd_path.write_text(json.dumps(prd_latest, indent=2) + "\n", encoding="utf-8")
            _append_progress(
                progress_path,
                sid,
                story["title"],
                res.get("summary", ""),
                res.get("learnings", ""),
            )
            _git(cwd, ["add", str(prd_path), str(progress_path)], check=True)
            _git(cwd, ["commit", "-m", f"chore(floating-loop): mark {sid} verified"], check=True)
            _git(cwd, ["branch", "-D", wbranch])

            merged_stories.append(sid)
            wave_merged += 1
            log(f"MERGED & VERIFIED {sid} ({story['title']})")

        if wave_merged == 0 and all(sid in exhausted_ids for sid in story_by_id):
            log("Circuit breaker: zero stories merged in wave and retries exhausted. Halting.")
            break

    final_prd = json.loads(prd_path.read_text(encoding="utf-8"))
    _, final_blocked, final_passed, final_total = _compute_ready_wave(final_prd, exhausted_ids)
    summary = {
        "branchName": feature_branch,
        "passed": final_passed,
        "total": final_total,
        "allPassed": final_passed == final_total,
        "mergedInRun": merged_stories,
        "exhausted": sorted(exhausted_ids),
        "blocked": final_blocked,
    }
    log(f"Swarm finished: {json.dumps(summary)}")
    return summary


result = await run_floating_swarm()
print(json.dumps(result, indent=2))
