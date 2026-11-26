#!/usr/bin/env python3
"""Unit and integration tests for scripts/stop-hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STOP_HOOK = REPO_ROOT / "scripts" / "stop-hook.py"
LOOP_CTL = REPO_ROOT / "scripts" / "loop-ctl.py"


def run_stop_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STOP_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),  # Intentionally run from plugin root to verify hook uses stdin cwd!
    )


def run_ctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LOOP_CTL), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


class TestStopHook(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "workspace"
        self.ws.mkdir(parents=True)
        self.logs = Path(self.tmp.name) / "logs"
        self.logs.mkdir(parents=True)
        self.transcript = self.logs / "transcript.jsonl"
        self.full_transcript = self.logs / "transcript_full.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _start_loop(self, cid: str = "conv-1", **kwargs) -> None:
        cmd = [
            "start",
            "--cwd",
            str(self.ws),
            "--prompt",
            kwargs.get("prompt", "Fix failing tests"),
            "--max-iterations",
            str(kwargs.get("max_iterations", 5)),
            "--completion-promise",
            kwargs.get("completion_promise", "DONE"),
            "--cb-threshold",
            str(kwargs.get("cb_threshold", 3)),
            "--max-context-tokens",
            str(kwargs.get("max_context_tokens", 180000)),
        ]
        if cid:
            cmd += ["--conversation-id", cid]
        if kwargs.get("verify_cmd"):
            cmd += ["--verify-cmd", kwargs["verify_cmd"]]
        res = run_ctl(*cmd)
        self.assertEqual(res.returncode, 0, res.stderr)

    def _base_payload(self, cid: str = "conv-1", **overrides) -> dict:
        p = {
            "conversationId": cid,
            "cwd": str(self.ws),
            "workspacePaths": [str(self.ws)],
            "transcriptPath": str(self.transcript),
            "terminationReason": "NO_TOOL_CALL",
            "fullyIdle": True,
            "continuationActive": False,
            "executionNum": 1,
            "hookEventName": "Stop",
        }
        p.update(overrides)
        return p

    def test_no_active_loop_exits_silently(self) -> None:
        res = run_stop_hook(self._base_payload())
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_session_isolation_ignores_other_conversation(self) -> None:
        self._start_loop(cid="conv-A")
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "working..."}],
        )
        res = run_stop_hook(self._base_payload(cid="conv-B"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_claim_pending_session_on_first_stop(self) -> None:
        self._start_loop(cid="")  # creates pending.json
        (self.ws / "work.txt").write_text("step 1\n", encoding="utf-8")
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "still working"}],
        )
        res = run_stop_hook(self._base_payload(cid="conv-claimed"))
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertEqual(out["decision"], "continue")
        self.assertTrue((self.ws / ".floating-loop" / "sessions" / "conv-claimed.json").is_file())
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "pending.json").exists())

    def test_thinking_tag_is_ignored_only_content_completes(self) -> None:
        self._start_loop(cid="conv-think")
        (self.ws / "a.py").write_text("x = 1\n", encoding="utf-8")
        # Promise appears ONLY inside thinking, NOT visible content -> must NOT stop!
        write_jsonl(
            self.transcript,
            [
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "thinking": "Maybe I should emit <promise>DONE</promise> later",
                    "content": "Running tests next.",
                }
            ],
        )
        res = run_stop_hook(self._base_payload(cid="conv-think"))
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertEqual(out["decision"], "continue")

    def test_truncated_transcript_falls_back_to_transcript_full(self) -> None:
        self._start_loop(cid="conv-trunc", completion_promise="ALL DONE")
        write_jsonl(
            self.transcript,
            [
                {
                    "step_index": 4,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Long output truncated...",
                    "truncated_fields": ["content"],
                }
            ],
        )
        write_jsonl(
            self.full_transcript,
            [
                {
                    "step_index": 4,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Long output truncated... finished!\n<promise>ALL   DONE</promise>",
                }
            ],
        )
        res = run_stop_hook(self._base_payload(cid="conv-trunc"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")  # Allowed stop!
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "conv-trunc.json").exists())

    def test_dual_gate_rejects_premature_promise_and_accepts_passing(self) -> None:
        flag = self.ws / "passing.flag"
        self._start_loop(
            cid="conv-dual",
            completion_promise="VERIFIED",
            verify_cmd=f"test -f '{flag}'",
        )
        write_jsonl(
            self.transcript,
            [
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Everything looks great! <promise>VERIFIED</promise>",
                }
            ],
        )
        # Attempt 1: flag missing -> Dual-Gate MUST reject and continue with backpressure!
        res1 = run_stop_hook(self._base_payload(cid="conv-dual"))
        self.assertEqual(res1.returncode, 0)
        out1 = json.loads(res1.stdout)
        self.assertEqual(out1["decision"], "continue")
        self.assertIn("DUAL-GATE REJECTION", out1["reason"])

        # Attempt 2: flag exists -> Dual-Gate passes and cleanly stops!
        flag.write_text("ok\n", encoding="utf-8")
        res2 = run_stop_hook(self._base_payload(cid="conv-dual"))
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(res2.stdout.strip(), "")
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "conv-dual.json").exists())

    def test_termination_reasons_user_canceled_and_error(self) -> None:
        self._start_loop(cid="conv-cancel")
        res_c = run_stop_hook(self._base_payload(cid="conv-cancel", terminationReason="USER_CANCELED"))
        self.assertEqual(res_c.returncode, 0)
        self.assertEqual(res_c.stdout.strip(), "")
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "conv-cancel.json").exists())

        self._start_loop(cid="conv-err")
        res_e = run_stop_hook(self._base_payload(cid="conv-err", terminationReason="MAX_INVOCATIONS"))
        self.assertEqual(res_e.returncode, 0)
        self.assertEqual(res_e.stdout.strip(), "")
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-err.json").read_text())
        self.assertEqual(state["status"], "paused")
        self.assertIn("MAX_INVOCATIONS", state["pauseReason"])

    def test_fully_idle_false_defers_to_background_tasks(self) -> None:
        self._start_loop(cid="conv-bg")
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Waiting on task."}],
        )
        res = run_stop_hook(self._base_payload(cid="conv-bg", fullyIdle=False))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")
        # State remains active at iteration 1
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-bg.json").read_text())
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["iteration"], 1)

    def test_context_token_ceiling_pauses_cleanly(self) -> None:
        self._start_loop(cid="conv-ctx", max_context_tokens=50000)
        write_jsonl(
            self.transcript,
            [
                {
                    "step_index": 3,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "input_tokens": 12000,
                    "cache_read_tokens": 42000,
                    "content": "Still working...",
                }
            ],
        )
        res = run_stop_hook(self._base_payload(cid="conv-ctx"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-ctx.json").read_text())
        self.assertEqual(state["status"], "paused")
        self.assertIn("Context ceiling reached", state["pauseReason"])

    def test_circuit_breaker_half_open_then_open_and_reset(self) -> None:
        self._start_loop(cid="conv-cb", max_iterations=10, cb_threshold=2)
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "No file edits made."}],
        )
        # Iteration 1 (no file changes) -> HALF_OPEN warning
        res1 = run_stop_hook(self._base_payload(cid="conv-cb"))
        out1 = json.loads(res1.stdout)
        self.assertEqual(out1["decision"], "continue")
        self.assertIn("HALF_OPEN", out1["reason"])

        # Iteration 2 (still zero file changes) -> OPEN -> halts cleanly
        res2 = run_stop_hook(self._base_payload(cid="conv-cb"))
        self.assertEqual(res2.stdout.strip(), "")
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-cb.json").read_text())
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["circuitBreaker"]["state"], "OPEN")

        # Reset circuit breaker via loop-ctl.py and confirm resumption works
        reset_res = run_ctl("reset-cb", "--cwd", str(self.ws), "--conversation-id", "conv-cb")
        self.assertEqual(reset_res.returncode, 0, reset_res.stderr)
        (self.ws / "fix.py").write_text("fixed = True\n", encoding="utf-8")
        res3 = run_stop_hook(self._base_payload(cid="conv-cb"))
        out3 = json.loads(res3.stdout)
        self.assertEqual(out3["decision"], "continue")

    def test_max_iterations_stops_loop(self) -> None:
        self._start_loop(cid="conv-max", max_iterations=1)
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Not done yet"}],
        )
        res = run_stop_hook(self._base_payload(cid="conv-max"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "conv-max.json").exists())

    def test_git_fingerprint_ignores_loop_telemetry_and_tracks_untracked_edits(self) -> None:
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "config", "user.email", "t@local"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "config", "user.name", "T"], check=True)
        (self.ws / "init.txt").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.ws), "add", "init.txt"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "commit", "-q", "-m", "init"], check=True)

        self._start_loop(cid="conv-git", max_iterations=10, cb_threshold=2)
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "working"}],
        )

        # Updating progress.txt alone must NOT count as code progress -> HALF_OPEN then OPEN
        (self.ws / "progress.txt").write_text("note 1\n", encoding="utf-8")
        r1 = run_stop_hook(self._base_payload(cid="conv-git"))
        self.assertIn("HALF_OPEN", json.loads(r1.stdout)["reason"])

        # Now create an untracked source file -> MUST reset streak back to CLOSED!
        (self.ws / "feature.py").write_text("v = 1\n", encoding="utf-8")
        r2 = run_stop_hook(self._base_payload(cid="conv-git"))
        out2 = json.loads(r2.stdout)
        self.assertEqual(out2["decision"], "continue")
        self.assertNotIn("HALF_OPEN", out2["reason"])

        # Editing the SAME untracked source file again -> still counts as real progress!
        (self.ws / "feature.py").write_text("v = 2\n", encoding="utf-8")
        r3 = run_stop_hook(self._base_payload(cid="conv-git"))
        out3 = json.loads(r3.stdout)
        self.assertEqual(out3["decision"], "continue")
        self.assertNotIn("HALF_OPEN", out3["reason"])

    def test_same_error_circuit_breaker_trips_even_with_file_churn(self) -> None:
        self._start_loop(
            cid="conv-same-err",
            max_iterations=10,
            completion_promise="DONE",
            verify_cmd="echo 'SyntaxError: line 42' >&2 && exit 1",
            cb_threshold=2,
        )
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "<promise>DONE</promise>"}],
        )
        # Iter 1: file changes, but verify fails with SyntaxError -> streak=1 (HALF_OPEN)
        (self.ws / "churn.py").write_text("attempt = 1\n", encoding="utf-8")
        r1 = run_stop_hook(self._base_payload(cid="conv-same-err"))
        out1 = json.loads(r1.stdout)
        self.assertEqual(out1["decision"], "continue")
        self.assertIn("DUAL-GATE REJECTION", out1["reason"])
        self.assertIn("HALF_OPEN", out1["reason"])

        # Iter 2: file changes again, SAME verify error -> streak=2 -> OPEN!
        (self.ws / "churn.py").write_text("attempt = 2\n", encoding="utf-8")
        r2 = run_stop_hook(self._base_payload(cid="conv-same-err"))
        self.assertEqual(r2.stdout.strip(), "")
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-same-err.json").read_text())
        self.assertEqual(state["status"], "paused")
        self.assertIn("Identical verification error", state["circuitBreaker"]["tripReason"])

    def test_same_error_circuit_breaker_ignores_volatile_test_runner_timings(self) -> None:
        # Simulate real pytest/unittest output whose duration, timestamp, address, and /tmp path
        # change on every single execution — sameErrorStreak MUST still recognize identical failure!
        counter = self.ws / "run_num.txt"
        verify_script = self.ws / "noisy_verify.py"
        verify_script.write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path('run_num.txt')\n"
            "n = int(p.read_text()) + 1 if p.exists() else 1\n"
            "p.write_text(str(n))\n"
            "print(f'2026-11-23T15:35:0{n}Z FAIL in /tmp/pytest-run-{n}/test_a.py obj=0x7f0000{n}a')\n"
            "print(f'Ran 4 tests in 0.0{n}7s (14{n}ms)')\n"
            "print('AssertionError: expected 5 != 4')\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        self._start_loop(
            cid="conv-noisy",
            max_iterations=10,
            completion_promise="DONE",
            verify_cmd=f"{sys.executable} '{verify_script}'",
            cb_threshold=2,
        )
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "<promise>DONE</promise>"}],
        )
        (self.ws / "edit.py").write_text("v = 1\n", encoding="utf-8")
        r1 = run_stop_hook(self._base_payload(cid="conv-noisy"))
        self.assertIn("HALF_OPEN", json.loads(r1.stdout)["reason"])

        (self.ws / "edit.py").write_text("v = 2\n", encoding="utf-8")
        r2 = run_stop_hook(self._base_payload(cid="conv-noisy"))
        self.assertEqual(r2.stdout.strip(), "")
        state = json.loads((self.ws / ".floating-loop" / "sessions" / "conv-noisy.json").read_text())
        self.assertEqual(state["status"], "paused")
        self.assertIn("Identical verification error", state["circuitBreaker"]["tripReason"])

    def test_verify_every_iteration_injects_backpressure_without_promise(self) -> None:
        res = run_ctl(
            "start",
            "--cwd",
            str(self.ws),
            "--conversation-id",
            "conv-every",
            "--prompt",
            "Make check pass",
            "--completion-promise",
            "DONE",
            "--verify-cmd",
            "echo 'FAILED: 2 tests' && exit 1",
            "--verify-every-iter",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        (self.ws / "a.py").write_text("1\n", encoding="utf-8")
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Working on fix..."}],
        )
        r = run_stop_hook(self._base_payload(cid="conv-every"))
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "continue")
        self.assertIn("BACKPRESSURE CHECK", out["reason"])
        self.assertIn("FAILED: 2 tests", out["reason"])

    def test_promise_literal_special_chars_and_last_tag_semantics(self) -> None:
        self._start_loop(cid="conv-special", completion_promise="100% [DONE] *ok*")
        write_jsonl(
            self.transcript,
            [
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Earlier I mentioned <promise>OTHER</promise>, now truly <promise>100%  [DONE]\n*ok*</promise>",
                }
            ],
        )
        res = run_stop_hook(self._base_payload(cid="conv-special"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")
        self.assertFalse((self.ws / ".floating-loop" / "sessions" / "conv-special.json").exists())

    def test_corrupted_state_file_archives_and_stops_safely(self) -> None:
        sdir = self.ws / ".floating-loop" / "sessions"
        sdir.mkdir(parents=True, exist_ok=True)
        bad = sdir / "conv-bad.json"
        bad.write_text(json.dumps({"status": "active", "conversationId": "conv-bad", "iteration": "not-an-int", "prompt": "x"}))
        res = run_stop_hook(self._base_payload(cid="conv-bad"))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")
        self.assertFalse(bad.exists())

    def test_hooks_json_stop_command_wiring(self) -> None:
        hooks = json.loads((REPO_ROOT / "hooks.json").read_text(encoding="utf-8"))
        cmd = hooks["floating-loop-stop"]["Stop"][0]["command"]
        self._start_loop(cid="conv-wire")
        (self.ws / "w.py").write_text("x = 1\n", encoding="utf-8")
        write_jsonl(
            self.transcript,
            [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "iter 1"}],
        )
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=str(REPO_ROOT),
            input=json.dumps(self._base_payload(cid="conv-wire")),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["decision"], "continue")


if __name__ == "__main__":
    unittest.main()
