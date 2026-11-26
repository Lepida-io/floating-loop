#!/usr/bin/env python3
"""Floating Loop CLI controller for state management, PRD DAG queries, and verification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_core import (  # noqa: E402
    archive_previous_run_if_branch_changed,
    archive_session,
    atomic_write_json,
    compute_waves,
    compute_workspace_fingerprint,
    default_circuit_breaker,
    detect_takeover_sources,
    escalate_effort,
    is_metric_improved,
    list_sessions,
    load_json_file,
    parse_last_float,
    pending_session_path,
    reset_circuit_breaker,
    session_path,
    takeover_in_session_ralph,
    takeover_prd_ralph,
    utc_now_iso,
    validate_prd,
)


def cmd_start(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    prompt = args.prompt or ""
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt.strip():
        sys.stderr.write("Error: Provide --prompt or --prompt-file with non-empty text.\n")
        return 1

    cid = (args.conversation_id or "").strip()
    target = session_path(cwd, cid) if cid else pending_session_path(cwd)
    now = utc_now_iso()
    ws_hash = compute_workspace_fingerprint(cwd)
    cb = default_circuit_breaker(args.cb_threshold)
    cb["lastWorkspaceHash"] = ws_hash

    state: Dict[str, Any] = {
        "version": 1,
        "status": "active",
        "conversationId": cid,
        "cwd": str(cwd),
        "prompt": prompt.strip(),
        "iteration": 1,
        "maxIterations": args.max_iterations,
        "completionPromise": args.completion_promise or "",
        "verifyCmd": args.verify_cmd or "",
        "verifyEveryIteration": bool(args.verify_every_iter),
        "verifyTimeout": args.verify_timeout,
        "maxContextTokens": args.max_context_tokens,
        "allowReset": bool(args.allow_reset),
        "circuitBreaker": cb,
        "startedAt": now,
        "updatedAt": now,
        "history": [],
    }
    atomic_write_json(target, state)

    if args.json:
        print(json.dumps({"ok": True, "statePath": str(target), "state": state}, indent=2))
        return 0

    max_str = str(args.max_iterations) if args.max_iterations > 0 else "unlimited"
    promise_str = f"<promise>{args.completion_promise}</promise>" if args.completion_promise else "none"
    print(f"Floating Loop activated ({'bound to ' + cid if cid else 'pending claim on first Stop hook'}).")
    print(f"  State file:        {target}")
    print(f"  Max iterations:    {max_str}")
    print(f"  Completion gate:   {promise_str}")
    if args.verify_cmd:
        print(f"  Dual-Gate verify:  {args.verify_cmd}")
    print(f"  Circuit breaker:   threshold={args.cb_threshold} (no-progress / repeat-error)")
    if args.max_context_tokens > 0:
        print(f"  Context ceiling:   {args.max_context_tokens:,} tokens")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    sessions = list_sessions(cwd)
    if args.conversation_id:
        sessions = [(p, s) for p, s in sessions if s.get("conversationId") == args.conversation_id]

    prd_summary = None
    if args.prd:
        prd_path = Path(args.prd)
        if not prd_path.is_absolute():
            prd_path = cwd / prd_path
        prd_data = load_json_file(prd_path)
        if prd_data is not None:
            prd_summary = compute_waves(prd_data)

    if args.json:
        print(
            json.dumps(
                {
                    "sessions": [{"path": str(p), "state": s} for p, s in sessions],
                    "prd": prd_summary,
                },
                indent=2,
            )
        )
        return 0

    if not sessions:
        print("No active or paused in-session Floating Loops.")
    else:
        print(f"In-session Floating Loops ({len(sessions)}):")
        for path, st in sessions:
            cid = st.get("conversationId") or "(pending)"
            cb = st.get("circuitBreaker") or {}
            print(
                f"  - {cid}: status={st.get('status')} iter={st.get('iteration')}/{st.get('maxIterations')} "
                f"cb={cb.get('state', 'CLOSED')} promise={st.get('completionPromise') or 'none'}"
            )
            if st.get("pauseReason"):
                print(f"    reason: {st['pauseReason']}")

    if prd_summary is not None:
        print(
            f"\nPRD ({prd_summary['project']} @ {prd_summary['branchName']}): "
            f"{prd_summary['passedCount']}/{prd_summary['total']} stories passed"
        )
        if prd_summary["readyWave"]:
            ids = ", ".join(s["id"] for s in prd_summary["readyWave"])
            print(f"  Ready wave: {ids}")
        if prd_summary["blocked"]:
            b_ids = ", ".join(f"{b['id']}(needs {','.join(b['missingDeps'])})" for b in prd_summary["blocked"])
            print(f"  Blocked:    {b_ids}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    sessions = list_sessions(cwd)
    targets = []
    if args.all:
        targets = sessions
    elif args.conversation_id:
        targets = [(p, s) for p, s in sessions if s.get("conversationId") == args.conversation_id]
    elif len(sessions) == 1:
        targets = sessions
    elif len(sessions) > 1:
        sys.stderr.write("Error: Multiple sessions exist. Pass --conversation-id <id> or --all.\n")
        return 1

    if not targets:
        print("No matching Floating Loop session found.")
        return 0

    for p, st in targets:
        st["status"] = "canceled"
        st["pauseReason"] = "Manually canceled via loop-ctl.py."
        st["updatedAt"] = utc_now_iso()
        arch = archive_session(cwd, st, p)
        print(
            f"Canceled Floating Loop (conversation={st.get('conversationId') or 'pending'}, "
            f"was at iteration {st.get('iteration')}). Archived to {arch}."
        )
    return 0


def cmd_reset_cb(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    sessions = list_sessions(cwd)
    targets = []
    if args.conversation_id:
        targets = [(p, s) for p, s in sessions if s.get("conversationId") == args.conversation_id]
    elif len(sessions) == 1:
        targets = sessions
    else:
        sys.stderr.write("Error: Specify --conversation-id when 0 or multiple sessions exist.\n")
        return 1

    if not targets:
        sys.stderr.write("Error: No matching session found.\n")
        return 1

    ws_hash = compute_workspace_fingerprint(cwd)
    for p, st in targets:
        cb = st.get("circuitBreaker")
        if not isinstance(cb, dict):
            cb = default_circuit_breaker(3)
            st["circuitBreaker"] = cb
        reset_circuit_breaker(cb, ws_hash)
        st["status"] = "active"
        st["pauseReason"] = ""
        st["updatedAt"] = utc_now_iso()
        if args.max_iterations is not None:
            st["maxIterations"] = args.max_iterations
        atomic_write_json(p, st)
        print(f"Reset circuit breaker and resumed loop for {st.get('conversationId') or 'pending'} (iter={st.get('iteration')}).")
    return 0


def cmd_validate_prd(args: argparse.Namespace) -> int:
    prd_path = Path(args.prd).resolve()
    data = load_json_file(prd_path)
    if data is None:
        msg = f"Failed to load valid JSON object from {prd_path}"
        if args.json:
            print(json.dumps({"valid": False, "errors": [msg], "warnings": []}, indent=2))
        else:
            sys.stderr.write(f"ERROR: {msg}\n")
        return 1

    errors, warnings = validate_prd(data)
    valid = len(errors) == 0 and (not args.strict or len(warnings) == 0)

    if args.json:
        print(json.dumps({"valid": valid, "errors": errors, "warnings": warnings}, indent=2))
    else:
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            sys.stderr.write(f"ERROR: {e}\n")
        if valid:
            waves = compute_waves(data)
            print(
                f"OK: {prd_path.name} valid ({waves['total']} stories, "
                f"{waves['passedCount']} passed, {len(waves['readyWave'])} ready in current wave)."
            )
    return 0 if valid else 1


def cmd_wave(args: argparse.Namespace, single: bool) -> int:
    prd_path = Path(args.prd).resolve()
    data = load_json_file(prd_path)
    if data is None:
        sys.stderr.write(f"Error: Unable to read {prd_path}\n")
        return 1
    errors, _ = validate_prd(data)
    if errors:
        sys.stderr.write("Error: Invalid PRD:\n" + "\n".join(f"  - {e}" for e in errors) + "\n")
        return 1

    info = compute_waves(data)
    if args.json:
        if single:
            out = {
                "allPassed": info["allPassed"],
                "passedCount": info["passedCount"],
                "total": info["total"],
                "branchName": info["branchName"],
                "verifyCommand": info["verifyCommand"],
                "defaultModel": info["defaultModel"],
                "defaultEffort": info["defaultEffort"],
                "story": info["nextStory"],
                "blockedCount": len(info["blocked"]),
            }
            print(json.dumps(out, indent=2))
        else:
            print(json.dumps(info, indent=2))
        return 0

    if info["allPassed"]:
        print("ALL_PASSED")
        return 0
    if single:
        story = info["nextStory"]
        if story is None:
            print("BLOCKED")
            return 2
        print(f"{story['id']}: {story['title']} (priority={story['priority']})")
    else:
        if not info["readyWave"]:
            print("BLOCKED")
            return 2
        for s in info["readyWave"]:
            print(f"{s['id']}: {s['title']} (priority={s['priority']}, hat={s.get('hat', 'implementer')})")
    return 0


def cmd_mark_story(args: argparse.Namespace) -> int:
    prd_path = Path(args.prd).resolve()
    data = load_json_file(prd_path)
    if data is None:
        sys.stderr.write(f"Error: Unable to read {prd_path}\n")
        return 1
    stories: List[Dict[str, Any]] = data.get("userStories") or []
    found = False
    for s in stories:
        if s.get("id") == args.id:
            s["passes"] = args.passes == "true"
            if args.note is not None:
                s["notes"] = args.note
            found = True
            break
    if not found:
        sys.stderr.write(f"Error: Story '{args.id}' not found in {prd_path}\n")
        return 1
    atomic_write_json(prd_path, data)
    print(f"Updated {args.id}: passes={args.passes}")
    return 0


def cmd_archive_check(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    prd_path = Path(args.prd)
    if not prd_path.is_absolute():
        prd_path = cwd / prd_path
    progress_path = Path(args.progress)
    if not progress_path.is_absolute():
        progress_path = cwd / progress_path
    dest = archive_previous_run_if_branch_changed(prd_path, progress_path, cwd)
    if dest is not None:
        print(f"Archived previous run to {dest}")
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    print(compute_workspace_fingerprint(args.cwd))
    return 0


def cmd_eval_metric(args: argparse.Namespace) -> int:
    raw = args.output if args.output is not None else sys.stdin.read()
    val = parse_last_float(raw)
    if val is None:
        if args.json:
            print(json.dumps({"ok": False, "error": "No numeric metric found in output"}))
        else:
            sys.stderr.write("Error: No numeric metric found in output.\n")
        return 1

    improved = True
    if args.best is not None:
        improved = is_metric_improved(val, float(args.best), args.direction)

    if args.json:
        print(json.dumps({"ok": True, "value": val, "improved": improved}))
    else:
        print(f"{val} {'IMPROVED' if improved else 'REGRESSED'}")
    return 0


def cmd_escalate_effort(args: argparse.Namespace) -> int:
    print(escalate_effort(args.current or ""))
    return 0


def cmd_takeover(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    detected = detect_takeover_sources(cwd)

    source_override = Path(args.source) if args.source else None
    if source_override is not None and not source_override.is_absolute():
        source_override = cwd / source_override

    prd_override = Path(args.prd) if args.prd else None
    if prd_override is not None and not prd_override.is_absolute():
        prd_override = cwd / prd_override

    in_session_src = source_override if (source_override and source_override.suffix == ".md") else detected["in_session"]
    prd_src = prd_override or (source_override if (source_override and source_override.suffix == ".json") else detected["prd"])

    kinds: List[str] = []
    if args.kind == "auto":
        if source_override is not None:
            kinds = ["prd"] if source_override.suffix == ".json" else ["in-session"]
        elif prd_override is not None:
            kinds = ["prd"]
        else:
            if in_session_src:
                kinds.append("in-session")
            if prd_src:
                kinds.append("prd")
            if len(kinds) > 1 and not args.all:
                sys.stderr.write(
                    "Multiple upstream Ralph state sources detected:\n"
                    f"  - in-session: {in_session_src}\n"
                    f"  - prd:        {prd_src}\n"
                    "Pass --kind in-session, --kind prd, or --all to take over both.\n"
                )
                return 1
    else:
        kinds = [args.kind]

    if not kinds:
        sys.stderr.write(
            f"No upstream Ralph state found in {cwd} "
            "(checked .claude/ralph-loop.local.md, .gemini/ralph-loop.local.md, and prd.json).\n"
        )
        return 1

    results: List[Dict[str, Any]] = []
    try:
        for k in kinds:
            if k == "in-session":
                if in_session_src is None or not in_session_src.is_file():
                    raise FileNotFoundError(f"In-session Ralph state file not found in {cwd}.")
                results.append(
                    takeover_in_session_ralph(
                        cwd=cwd,
                        source_path=in_session_src,
                        conversation_id=args.conversation_id,
                        verify_cmd=args.verify_cmd,
                        max_iterations_override=args.max_iterations,
                        cb_threshold=args.cb_threshold,
                        max_context_tokens=args.max_context_tokens,
                        allow_reset=bool(args.allow_reset),
                        keep_source=bool(args.keep_source),
                    )
                )
            elif k == "prd":
                if prd_src is None or not prd_src.is_file():
                    raise FileNotFoundError(f"PRD file not found in {cwd}.")
                results.append(
                    takeover_prd_ralph(
                        cwd=cwd,
                        prd_path=prd_src,
                        verify_cmd=args.verify_cmd,
                        add_sequential_deps=bool(args.add_sequential_deps),
                    )
                )
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if args.json:
        print(json.dumps({"ok": True, "results": results}, indent=2))
        return 0

    for res in results:
        if res["kind"] == "in-session":
            print(f"Took over in-session Ralph loop -> {res['statePath']}")
            print(
                f"  Resuming at iteration {res['iteration']}/"
                f"{res['maxIterations'] or 'unlimited'} | promise={res['completionPromise'] or 'none'}"
            )
            if res["verifyCmd"]:
                print(f"  Dual-Gate verify:  {res['verifyCmd']}")
            if res["archivedSource"]:
                print(f"  Archived old file: {res['archivedSource']}")
        elif res["kind"] == "prd":
            print(
                f"Prepared snarktank/ralph PRD takeover: {res['project']} "
                f"({res['passedCount']}/{res['total']} already passed, next={res['nextStory'] or 'ALL_PASSED'})"
            )
            print(f"  Target branch:     {res['branchName']} (current git HEAD: {res['currentGitBranch'] or 'n/a'})")
            print(f"  progress.txt:      {'preserved' if res['progressTxtExists'] else 'not present yet'}")
            print(f"  Explicit DAG deps: {'yes' if res['hasExplicitDependsOn'] else 'no (safe in serial mode; swarm auto-serializes)'}")
            if res["claudeMdFiles"]:
                print(f"  CLAUDE.md files:   {', '.join(res['claudeMdFiles'])} (read automatically by prd-iteration.md)")
            if res["dirtyFiles"]:
                print(f"  Interrupted edits: {len(res['dirtyFiles'])} uncommitted path(s) detected — will be surfaced to next iteration")
            if res["backupPath"]:
                print(f"  PRD backup:        {res['backupPath']}")
            verify_flag = f" --verify-cmd '{res['verifyCommand']}'" if res["verifyCommand"] else ""
            print(
                f"  Next command:      bash {SCRIPT_DIR / 'floating-loop.sh'} --mode prd "
                f"--cwd \"{cwd}\" --prd \"{res['prdPath']}\"{verify_flag}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loop-ctl.py", description="Floating Loop controller")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("start", help="Initialize an in-session Stop-hook Floating Loop")
    sp.add_argument("--cwd", default=".", help="Workspace directory")
    sp.add_argument("--conversation-id", default="", help="Antigravity conversationId (recommended)")
    sp.add_argument("--prompt", default="", help="Loop prompt text")
    sp.add_argument("--prompt-file", default="", help="Read prompt from file")
    sp.add_argument("--max-iterations", type=int, default=15, help="Max iterations (0 = unlimited)")
    sp.add_argument("--completion-promise", default="DONE", help="Promise text inside <promise>...</promise>")
    sp.add_argument("--verify-cmd", default="", help="Dual-Gate independent verification shell command")
    sp.add_argument("--verify-every-iter", action="store_true", help="Run verify-cmd on every iteration, not just on promise")
    sp.add_argument("--verify-timeout", type=int, default=90, help="Verify command timeout in seconds")
    sp.add_argument("--max-context-tokens", type=int, default=180000, help="Pause in-session loop above token ceiling (0=disable)")
    sp.add_argument("--cb-threshold", type=int, default=3, help="Circuit breaker stall/repeat-error threshold (0=disable)")
    sp.add_argument("--allow-reset", action="store_true", help="Allow git reset --hard during active loop (autoresearch)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("status", help="Show active loops and optional PRD progress")
    sp.add_argument("--cwd", default=".")
    sp.add_argument("--conversation-id", default="")
    sp.add_argument("--prd", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("cancel", help="Cancel an active in-session loop")
    sp.add_argument("--cwd", default=".")
    sp.add_argument("--conversation-id", default="")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("reset-cb", help="Reset a tripped circuit breaker or paused session")
    sp.add_argument("--cwd", default=".")
    sp.add_argument("--conversation-id", default="")
    sp.add_argument("--max-iterations", type=int, default=None)
    sp.set_defaults(func=cmd_reset_cb)

    sp = sub.add_parser("validate-prd", help="Validate prd.json schema and DAG dependencies")
    sp.add_argument("prd", help="Path to prd.json")
    sp.add_argument("--strict", action="store_true", help="Fail on warnings too")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_validate_prd)

    sp = sub.add_parser("next-story", help="Print next highest-priority unblocked failing story")
    sp.add_argument("prd")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=lambda a: cmd_wave(a, single=True))

    sp = sub.add_parser("next-wave", help="Print all currently unblocked failing stories for parallel execution")
    sp.add_argument("prd")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=lambda a: cmd_wave(a, single=False))

    sp = sub.add_parser("mark-story", help="Update a story's passes flag and optional note in prd.json")
    sp.add_argument("prd")
    sp.add_argument("--id", required=True)
    sp.add_argument("--passes", choices=["true", "false"], required=True)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_mark_story)

    sp = sub.add_parser("archive-check", help="Archive previous run if branchName changed")
    sp.add_argument("--cwd", default=".")
    sp.add_argument("--prd", default="prd.json")
    sp.add_argument("--progress", default="progress.txt")
    sp.set_defaults(func=cmd_archive_check)

    sp = sub.add_parser("fingerprint", help="Print workspace progress SHA-256 fingerprint")
    sp.add_argument("--cwd", default=".")
    sp.set_defaults(func=cmd_fingerprint)

    sp = sub.add_parser("eval-metric", help="Parse and compare autoresearch numeric metric")
    sp.add_argument("--output", default=None)
    sp.add_argument("--best", type=float, default=None)
    sp.add_argument("--direction", choices=["minimize", "maximize"], default="minimize")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_eval_metric)

    sp = sub.add_parser("escalate-effort", help="Return next higher reasoning effort tier")
    sp.add_argument("--current", default="")
    sp.set_defaults(func=cmd_escalate_effort)

    sp = sub.add_parser("takeover", help="Take over an in-progress claude-ralph-loop or snarktank/ralph run")
    sp.add_argument("--cwd", default=".", help="Target workspace directory")
    sp.add_argument("--kind", choices=["auto", "in-session", "prd"], default="auto", help="Takeover mode")
    sp.add_argument("--all", action="store_true", help="Take over both in-session and PRD state if both exist")
    sp.add_argument("--source", default="", help="Explicit path to legacy ralph-loop.local.md or prd.json")
    sp.add_argument("--prd", default="", help="Explicit path to prd.json for PRD takeover")
    sp.add_argument("--conversation-id", default="", help="Antigravity conversationId to bind in-session loop")
    sp.add_argument("--verify-cmd", default="", help="Dual-Gate verify command to attach on takeover")
    sp.add_argument("--max-iterations", type=int, default=None, help="Override max iterations for in-session takeover")
    sp.add_argument("--cb-threshold", type=int, default=3, help="Circuit breaker threshold for in-session takeover")
    sp.add_argument("--max-context-tokens", type=int, default=180000, help="Context ceiling for in-session takeover")
    sp.add_argument("--allow-reset", action="store_true", help="Permit git reset --hard during active in-session loop")
    sp.add_argument("--keep-source", action="store_true", help="Do not archive/remove legacy .claude/ralph-loop.local.md")
    sp.add_argument(
        "--add-sequential-deps",
        action="store_true",
        help="Add explicit sequential dependsOn edges (priority order) to legacy prd.json",
    )
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_takeover)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
