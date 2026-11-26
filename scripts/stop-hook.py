#!/usr/bin/env python3
"""Antigravity Stop hook for Floating Loop in-session autonomous execution.

Reads camelCase Stop hook payload from stdin and either:
- Allows stop (exits 0 with empty stdout) when inactive, completed, canceled, paused, or tripped.
- Emits {"decision": "continue", "reason": "..."} to re-enter the loop with backpressure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_core import (  # noqa: E402
    archive_session,
    atomic_write_json,
    compute_workspace_fingerprint,
    default_circuit_breaker,
    promise_matches,
    read_latest_planner_response,
    resolve_session_for_hook,
    run_verify_command,
    update_circuit_breaker,
    utc_now_iso,
)

NON_CONTINUABLE_REASONS = {
    "ERROR",
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
    "TERMINAL_STEP_TYPE",
    "TERMINAL_CUSTOM_HOOK",
    "INJECTED_RESPONSE",
}


def emit_continue(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "continue", "reason": reason}) + "\n")


def log_err(msg: str) -> None:
    sys.stderr.write(f"[floating-loop] {msg}\n")


def resolve_cwd(payload: Dict[str, Any]) -> str:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    ws = payload.get("workspacePaths")
    if isinstance(ws, list) and ws and isinstance(ws[0], str) and ws[0].strip():
        return ws[0].strip()
    return ""


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    cwd = resolve_cwd(payload)
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not cwd or not conversation_id:
        return 0

    state_path, state = resolve_session_for_hook(cwd, conversation_id, claim_pending=True)
    if state_path is None or state is None:
        return 0

    if state.get("status") != "active":
        return 0

    # Validate prompt and iteration metadata
    prompt_text = str(state.get("prompt") or "").strip()
    iteration = state.get("iteration")
    max_iterations = state.get("maxIterations", 0)
    if not prompt_text or not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        log_err("Session state corrupted (missing prompt or invalid iteration). Archiving and stopping.")
        state["status"] = "corrupted"
        state["updatedAt"] = utc_now_iso()
        archive_session(cwd, state, state_path)
        return 0
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 0:
        log_err("Session state corrupted (invalid maxIterations). Archiving and stopping.")
        state["status"] = "corrupted"
        state["updatedAt"] = utc_now_iso()
        archive_session(cwd, state, state_path)
        return 0

    # 1. Respect Antigravity terminationReason
    term_reason = str(payload.get("terminationReason") or "").strip()
    if term_reason == "USER_CANCELED":
        state["status"] = "canceled"
        state["pauseReason"] = "Canceled by user (USER_CANCELED)."
        state["updatedAt"] = utc_now_iso()
        archive_session(cwd, state, state_path)
        log_err("Loop canceled due to USER_CANCELED.")
        return 0

    if term_reason and term_reason != "NO_TOOL_CALL":
        if term_reason in NON_CONTINUABLE_REASONS or term_reason != "":
            state["status"] = "paused"
            state["pauseReason"] = (
                f"Runtime stopped with terminationReason={term_reason}. "
                "Resume with loop-ctl.py reset-cb or switch to scripts/floating-loop.sh for fresh context."
            )
            state["updatedAt"] = utc_now_iso()
            atomic_write_json(state_path, state)
            log_err(state["pauseReason"])
            return 0

    # 2. If background tasks are still running, let reactive wakeup handle it instead of double-injecting
    if payload.get("fullyIdle") is False:
        return 0

    # 3. Inspect Antigravity transcript (PLANNER_RESPONSE.content only; fallback to transcript_full.jsonl)
    transcript_path = str(payload.get("transcriptPath") or "").strip()
    tr = read_latest_planner_response(transcript_path)
    if not tr.found:
        log_err(f"Transcript unavailable ({tr.error}); pausing loop safely.")
        state["status"] = "paused"
        state["pauseReason"] = f"Transcript read failure: {tr.error}"
        state["updatedAt"] = utc_now_iso()
        atomic_write_json(state_path, state)
        return 0

    # 4. Context-rot protection via Antigravity transcript token telemetry
    max_ctx_tokens = int(state.get("maxContextTokens") or 0)
    if max_ctx_tokens > 0 and tr.total_context_tokens >= max_ctx_tokens:
        state["status"] = "paused"
        state["lastContextTokens"] = tr.total_context_tokens
        state["pauseReason"] = (
            f"Context ceiling reached ({tr.total_context_tokens:,} >= {max_ctx_tokens:,} tokens). "
            "Run /compact or switch to scripts/floating-loop.sh for fresh-context execution, "
            "then resume with loop-ctl.py reset-cb if continuing in-session."
        )
        state["updatedAt"] = utc_now_iso()
        atomic_write_json(state_path, state)
        log_err(state["pauseReason"])
        return 0

    # 5. Dual-Gate Completion Verification (<promise> + independent verifyCmd)
    expected_promise = str(state.get("completionPromise") or "").strip()
    verify_cmd = str(state.get("verifyCmd") or "").strip()
    verify_every = bool(state.get("verifyEveryIteration", False))
    verify_timeout = int(state.get("verifyTimeout", 90))

    matched_promise = bool(expected_promise) and promise_matches(tr.content, expected_promise)
    backpressure_msg = ""
    verify_error_for_cb = ""

    if matched_promise:
        if verify_cmd:
            passed, rc, tail = run_verify_command(verify_cmd, cwd, timeout=verify_timeout)
            if passed:
                state["status"] = "completed"
                state["completedAt"] = utc_now_iso()
                state["updatedAt"] = state["completedAt"]
                state["lastContextTokens"] = tr.total_context_tokens
                archive_session(cwd, state, state_path)
                log_err(
                    f"Completed at iteration {iteration}: <promise>{expected_promise}</promise> "
                    f"verified by `{verify_cmd}`."
                )
                return 0
            # Dual-Gate rejection: promise was premature!
            verify_error_for_cb = f"exit={rc}\n{tail}"
            backpressure_msg = (
                f"DUAL-GATE REJECTION: You output <promise>{expected_promise}</promise>, "
                f"but the independent verification command `{verify_cmd}` FAILED (exit {rc}).\n"
                "Fix the root cause before asserting completion again. Verification output:\n"
                f"```\n{tail or '(no output)'}\n```"
            )
        else:
            state["status"] = "completed"
            state["completedAt"] = utc_now_iso()
            state["updatedAt"] = state["completedAt"]
            state["lastContextTokens"] = tr.total_context_tokens
            archive_session(cwd, state, state_path)
            log_err(f"Completed at iteration {iteration}: detected <promise>{expected_promise}</promise>.")
            return 0
    elif verify_every and verify_cmd:
        passed, rc, tail = run_verify_command(verify_cmd, cwd, timeout=verify_timeout)
        if not passed:
            verify_error_for_cb = f"exit={rc}\n{tail}"
            backpressure_msg = (
                f"BACKPRESSURE CHECK: `{verify_cmd}` failed (exit {rc}):\n"
                f"```\n{tail or '(no output)'}\n```"
            )

    # 6. Max-iterations guard (checked AFTER evaluating final iteration's completion!)
    if max_iterations > 0 and iteration >= max_iterations:
        state["status"] = "max_iterations_reached"
        state["updatedAt"] = utc_now_iso()
        state["lastContextTokens"] = tr.total_context_tokens
        archive_session(cwd, state, state_path)
        log_err(f"Stopped: reached max_iterations ({max_iterations}).")
        return 0

    # 7. 3-State Circuit Breaker (workspace fingerprint + repeat-error streak)
    cb = state.get("circuitBreaker")
    if not isinstance(cb, dict):
        cb = default_circuit_breaker(3)
        state["circuitBreaker"] = cb

    ws_hash = compute_workspace_fingerprint(cwd)
    update_circuit_breaker(cb, ws_hash, verify_error_for_cb)

    if cb.get("state") == "OPEN":
        state["status"] = "paused"
        state["pauseReason"] = f"Circuit breaker OPEN: {cb.get('tripReason', 'stalled')}"
        state["updatedAt"] = utc_now_iso()
        state["lastContextTokens"] = tr.total_context_tokens
        atomic_write_json(state_path, state)
        log_err(
            f"{state['pauseReason']} Inspect workspace and run "
            f"`python3 scripts/loop-ctl.py reset-cb --cwd \"{cwd}\" --conversation-id \"{conversation_id}\"` to resume."
        )
        return 0

    # 8. Advance iteration and emit continuation directive
    next_iteration = iteration + 1
    state["iteration"] = next_iteration
    state["updatedAt"] = utc_now_iso()
    state["lastContextTokens"] = tr.total_context_tokens
    state["executionNum"] = payload.get("executionNum", 0)
    state["continuationActive"] = bool(payload.get("continuationActive", False))

    history = state.setdefault("history", [])
    if isinstance(history, list):
        history.append(
            {
                "iteration": iteration,
                "timestamp": state["updatedAt"],
                "contextTokens": tr.total_context_tokens,
                "workspaceHash": ws_hash[:12],
                "cbState": cb.get("state", "CLOSED"),
                "promiseRejected": bool(matched_promise and backpressure_msg),
            }
        )
        # Keep bounded history in state file
        if len(history) > 50:
            state["history"] = history[-50:]

    atomic_write_json(state_path, state)

    iter_label = f"{next_iteration}/{max_iterations}" if max_iterations > 0 else f"{next_iteration}/∞"
    header_parts = [f"[Floating Loop — Iteration {iter_label}]"]
    if tr.total_context_tokens > 0:
        if max_ctx_tokens > 0:
            pct = int((tr.total_context_tokens / max_ctx_tokens) * 100)
            header_parts.append(f"Context: {tr.total_context_tokens:,}/{max_ctx_tokens:,} tokens ({pct}%)")
        else:
            header_parts.append(f"Context: {tr.total_context_tokens:,} tokens")
    if cb.get("state") == "HALF_OPEN":
        header_parts.append(
            "CIRCUIT BREAKER HALF_OPEN: You are 1 stalled or identical-error iteration away from auto-halt. "
            "Diagnose the root cause and try a materially different approach."
        )

    sections = [" | ".join(header_parts[:2])]
    if len(header_parts) > 2:
        sections.append(header_parts[2])
    if backpressure_msg:
        sections.append(backpressure_msg)
    sections.append(prompt_text)

    if expected_promise:
        gate_note = f" and `{verify_cmd}` passes" if verify_cmd else ""
        sections.append(
            f"Completion rule: Output `<promise>{expected_promise}</promise>` ONLY when the task is genuinely complete"
            f"{gate_note}. Never fabricate completion or skip tests."
        )
    else:
        sections.append("No completion promise configured; loop continues until max_iterations or cancellation.")

    emit_continue("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
