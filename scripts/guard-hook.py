#!/usr/bin/env python3
"""Antigravity PreToolUse safety guardrail for active Floating Loops.

Outputs empty stdout (exit 0) for passthrough so normal user sessions and
allowed tool calls incur zero friction and preserve Antigravity's default
permission model (Antigravity treats `{}` without a `decision` field as DENY).
Only emits `{"decision": "deny", "reason": "..."}` when blocking a dangerous
or state-tampering call during an active loop.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loop_core import (  # noqa: E402
    SESSIONS_DIR_NAME,
    STATE_DIR_NAME,
    load_json_file,
    parse_iso_utc,
    pending_session_path,
    resolve_session_for_hook,
)

# ---------------------------------------------------------------------------
# Floating Loop Guard Patterns
# ---------------------------------------------------------------------------

CATASTROPHIC_RM_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+"
    r"(?:(?:-[a-zA-Z]+|--recursive|--force|--no-preserve-root)\s+)*"
    r"(?:--\s+)?"
    r"(?:/(?:\*|\s|$)|~(?:/|\*|\s|$)|\"?\$HOME\"?(?:/|\*|\s|$))"
)
RM_RECURSIVE_OR_FORCE_RE = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+[^\n;&|]*(?:-[a-zA-Z]*[rR]|--recursive)")
FORCE_PUSH_RE = re.compile(r"\bgit\s+push\b[^\n;&|]*(?:\s--force\b|\s-f\b)")
HARD_RESET_RE = re.compile(r"\bgit\s+reset\b[^\n;&|]*\s--hard\b")
STATE_DIR_SHELL_RE = re.compile(r"(?:^|[\s'\"/])\.floating-loop(?:/sessions(?:/|$)|(?:\s|$|['\";&|]))")
SAFE_LOOP_CTL_CMD_RE = re.compile(r"^\s*(?:python3?\s+)?(?:\S*/)?loop-ctl\.py(?:\s|$)")
SHELL_META_RE = re.compile(r"[;&|`$]|\n|>{1,2}")


def emit_json(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")


def deny(reason: str) -> int:
    emit_json({"decision": "deny", "reason": reason})
    return 0


def is_safe_loop_ctl_invocation(cmd: str) -> bool:
    """Allow direct loop-ctl.py invocations only when free of shell chaining/redirection."""
    if SHELL_META_RE.search(cmd):
        return False
    return bool(SAFE_LOOP_CTL_CMD_RE.match(cmd))


def resolve_cwd(payload: Dict[str, Any]) -> str:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    ws = payload.get("workspacePaths")
    if isinstance(ws, list) and ws and isinstance(ws[0], str) and ws[0].strip():
        return ws[0].strip()
    return ""


def is_session_state_path(target_path: str, cwd: str) -> bool:
    if not target_path:
        return False
    try:
        p = Path(target_path)
        if not p.is_absolute():
            p = Path(cwd) / p
        resolved = p.resolve()
        sess_root = (Path(cwd).resolve() / STATE_DIR_NAME / SESSIONS_DIR_NAME).resolve()
        return resolved == sess_root or sess_root in resolved.parents
    except OSError:
        return f"{STATE_DIR_NAME}/{SESSIONS_DIR_NAME}" in target_path.replace("\\", "/")


def resolve_active_state_for_guard(cwd: str, conversation_id: str) -> Optional[Dict[str, Any]]:
    """Resolve active session for conversationId, or fall back read-only to fresh pending.json."""
    _, state = resolve_session_for_hook(cwd, conversation_id, claim_pending=False)
    if state is not None and state.get("status") == "active":
        return state
    pend = load_json_file(pending_session_path(cwd))
    if pend is not None and pend.get("status") == "active" and not pend.get("conversationId"):
        created = parse_iso_utc(str(pend.get("updatedAt") or pend.get("startedAt") or ""))
        now = dt.datetime.now(dt.timezone.utc)
        if created is None or (now - created).total_seconds() <= 3600:
            return pend
    return None


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

    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return 0

    tool_name = str(tool_call.get("name") or "").strip()
    args = tool_call.get("args")
    if not isinstance(args, dict):
        args = {}

    cwd = resolve_cwd(payload)
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not cwd or not conversation_id:
        return 0

    state = resolve_active_state_for_guard(cwd, conversation_id)
    if state is None:
        return 0

    if tool_name in {"edit_file", "write_file"}:
        target = str(args.get("Path") or args.get("path") or "")
        if is_session_state_path(target, cwd):
            return deny(
                "Floating Loop Guard: Direct edits to .floating-loop/sessions/ state files are blocked "
                "during an active loop. Use scripts/loop-ctl.py (status/cancel/reset-cb) instead."
            )

    if tool_name == "run_command":
        cmd = str(args.get("CommandLine") or args.get("command") or "")
        if CATASTROPHIC_RM_RE.search(cmd) and RM_RECURSIVE_OR_FORCE_RE.search(cmd):
            return deny(
                "Floating Loop Guard: Blocked catastrophic recursive rm targeting root or home directory "
                "during an active autonomous loop."
            )
        if FORCE_PUSH_RE.search(cmd):
            return deny(
                "Floating Loop Guard: Blocked `git push --force` / `-f` during an active autonomous loop."
            )
        if HARD_RESET_RE.search(cmd) and not bool(state.get("allowReset", False)):
            return deny(
                "Floating Loop Guard: Blocked `git reset --hard` during an active loop "
                "(only permitted when allowReset is enabled for autoresearch mode)."
            )
        if STATE_DIR_SHELL_RE.search(cmd) and not is_safe_loop_ctl_invocation(cmd):
            return deny(
                "Floating Loop Guard: Direct shell operations on .floating-loop/ state are blocked "
                "during an active loop. Use scripts/loop-ctl.py directly without shell chaining."
            )

    # Empty stdout (exit 0) = true passthrough preserving default Antigravity permission policies.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
