#!/usr/bin/env python3
"""Unit and adversarial bypass tests for scripts/guard-hook.py (PreToolUse safety guard)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_HOOK = REPO_ROOT / "scripts" / "guard-hook.py"
LOOP_CTL = REPO_ROOT / "scripts" / "loop-ctl.py"


def run_guard(payload: dict) -> Optional[Dict[str, Any]]:
    """Run guard-hook.py and return parsed JSON if non-empty, or None for true empty-stdout passthrough."""
    res = subprocess.run(
        [sys.executable, str(GUARD_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, res.stderr
    stripped = res.stdout.strip()
    if not stripped:
        return None
    return json.loads(stripped)


class TestGuardHook(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _start_loop(self, cid: str = "conv-guard", allow_reset: bool = False) -> None:
        cmd = [
            sys.executable,
            str(LOOP_CTL),
            "start",
            "--cwd",
            str(self.ws),
            "--prompt",
            "Run task",
        ]
        if cid:
            cmd += ["--conversation-id", cid]
        if allow_reset:
            cmd.append("--allow-reset")
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def _payload(self, cid: str, tool_name: str, args: dict) -> dict:
        return {
            "conversationId": cid,
            "cwd": str(self.ws),
            "workspacePaths": [str(self.ws)],
            "hookEventName": "PreToolUse",
            "toolCall": {"id": "tc-1", "name": tool_name, "args": args},
        }

    def test_noop_outside_active_loop_emits_empty_stdout_not_braces(self) -> None:
        # Critical Antigravity contract: `{}` without `decision` is treated as DENY;
        # true passthrough MUST be empty stdout (None).
        out = run_guard(self._payload("conv-none", "run_command", {"CommandLine": "git reset --hard HEAD~1"}))
        self.assertIsNone(out)

    def test_blocks_direct_state_file_edit_and_allows_normal_file_write(self) -> None:
        self._start_loop("conv-guard")
        target = str(self.ws / ".floating-loop" / "sessions" / "conv-guard.json")
        out = run_guard(self._payload("conv-guard", "edit_file", {"Path": target}))
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("sessions", out.get("reason", ""))

        # Normal source file writes must pass through with empty stdout
        ok = run_guard(self._payload("conv-guard", "write_file", {"Path": str(self.ws / "app.py")}))
        self.assertIsNone(ok)

    def test_blocks_catastrophic_rm_variants_and_force_push(self) -> None:
        self._start_loop("conv-guard")
        for bad_cmd in (
            "rm -rf /",
            "rm -rf /*",
            "rm -r -f /",
            "rm -rf -- /",
            "sudo rm -rf /",
            "rm -fr ~/",
            "rm -rf $HOME",
            "git push origin main --force",
            "git push -f origin feat",
        ):
            out = run_guard(self._payload("conv-guard", "run_command", {"CommandLine": bad_cmd}))
            self.assertIsNotNone(out, f"Expected deny for: {bad_cmd}")
            self.assertEqual(out.get("decision"), "deny", f"Expected deny for: {bad_cmd}")

    def test_blocks_adversarial_state_dir_bypasses(self) -> None:
        self._start_loop("conv-guard")
        for bypass_cmd in (
            "rm -rf .floating-loop/sessions/conv-guard.json",
            "rm -rf .floating-loop",
            "rm -rf .floating-loop/sessions/ # loop-ctl.py",
            "python3 scripts/loop-ctl.py status && rm -rf .floating-loop",
            "echo '{}' > .floating-loop/sessions/conv-guard.json",
            "mv .floating-loop /tmp/gone",
        ):
            out = run_guard(self._payload("conv-guard", "run_command", {"CommandLine": bypass_cmd}))
            self.assertIsNotNone(out, f"Bypass should be denied: {bypass_cmd}")
            self.assertEqual(out.get("decision"), "deny", f"Bypass should be denied: {bypass_cmd}")

        # Direct unchained loop-ctl.py invocation passes through cleanly
        out_ctl = run_guard(
            self._payload("conv-guard", "run_command", {"CommandLine": "python3 scripts/loop-ctl.py status --cwd ."})
        )
        self.assertIsNone(out_ctl)

    def test_pending_session_protects_turn_one_before_first_stop(self) -> None:
        self._start_loop(cid="")  # creates pending.json
        out = run_guard(self._payload("conv-first-turn", "run_command", {"CommandLine": "git reset --hard HEAD~1"}))
        self.assertIsNotNone(out)
        self.assertEqual(out.get("decision"), "deny")

        ok = run_guard(self._payload("conv-first-turn", "write_file", {"Path": str(self.ws / "calc.py")}))
        self.assertIsNone(ok)

    def test_hard_reset_blocked_unless_allow_reset(self) -> None:
        self._start_loop("conv-no-reset", allow_reset=False)
        out_blocked = run_guard(self._payload("conv-no-reset", "run_command", {"CommandLine": "git reset --hard HEAD~1"}))
        self.assertEqual(out_blocked.get("decision"), "deny")

        self._start_loop("conv-allow-reset", allow_reset=True)
        out_ok = run_guard(self._payload("conv-allow-reset", "run_command", {"CommandLine": "git reset --hard HEAD~1"}))
        self.assertIsNone(out_ok)  # Never emits "allow"!

    def test_blocks_relative_traversal_write_and_allows_safe_commands(self) -> None:
        self._start_loop("conv-guard")
        traversal = f"{self.ws}/subdir/../.floating-loop/sessions/conv-guard.json"
        out_w = run_guard(self._payload("conv-guard", "write_file", {"Path": traversal}))
        self.assertEqual(out_w.get("decision"), "deny")

        out_safe = run_guard(
            self._payload("conv-guard", "run_command", {"CommandLine": "rm -rf build/dist && git push origin feat"})
        )
        self.assertIsNone(out_safe)

    def test_session_isolation_and_hooks_json_wiring(self) -> None:
        self._start_loop("conv-A")
        # Different conversation should not be blocked
        out_other = run_guard(self._payload("conv-B", "run_command", {"CommandLine": "git reset --hard HEAD~1"}))
        self.assertIsNone(out_other)

        # Verify exact command from hooks.json executes cleanly from plugin root
        hooks = json.loads((REPO_ROOT / "hooks.json").read_text(encoding="utf-8"))
        cmd = hooks["floating-loop-guard"]["PreToolUse"][0]["hooks"][0]["command"]
        res_deny = subprocess.run(
            cmd,
            shell=True,
            cwd=str(REPO_ROOT),
            input=json.dumps(self._payload("conv-A", "run_command", {"CommandLine": "git push --force"})),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_deny.returncode, 0, res_deny.stderr)
        self.assertEqual(json.loads(res_deny.stdout).get("decision"), "deny")

        res_pass = subprocess.run(
            cmd,
            shell=True,
            cwd=str(REPO_ROOT),
            input=json.dumps(self._payload("conv-A", "write_file", {"Path": str(self.ws / "ok.py")})),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_pass.returncode, 0, res_pass.stderr)
        self.assertEqual(res_pass.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
