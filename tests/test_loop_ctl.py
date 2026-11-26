#!/usr/bin/env python3
"""Unit tests for loop_core.py, loop-ctl.py, PRD DAG validation, and floating_swarm.py syntax."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import loop_core  # noqa: E402

LOOP_CTL = SCRIPTS_DIR / "loop-ctl.py"
EXAMPLE_PRD = REPO_ROOT / "examples" / "prd.json.example"
SWARM_WORKFLOW = SCRIPTS_DIR / "workflows" / "floating_swarm.py"


class TestLoopCoreAndCtl(unittest.TestCase):
    def test_example_prd_validates_strict_and_computes_waves(self) -> None:
        res = subprocess.run(
            [sys.executable, str(LOOP_CTL), "validate-prd", str(EXAMPLE_PRD), "--strict", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data["valid"])
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["warnings"], [])

        prd = json.loads(EXAMPLE_PRD.read_text(encoding="utf-8"))
        wave1 = loop_core.compute_waves(prd)
        self.assertEqual([s["id"] for s in wave1["readyWave"]], ["US-001"])
        self.assertEqual(len(wave1["blocked"]), 3)

        # Simulate US-001 passing -> US-002 and US-003 become parallel ready wave!
        prd["userStories"][0]["passes"] = True
        wave2 = loop_core.compute_waves(prd)
        self.assertEqual([s["id"] for s in wave2["readyWave"]], ["US-002", "US-003"])
        self.assertEqual([b["id"] for b in wave2["blocked"]], ["US-004"])

    def test_validate_prd_detects_cycles_and_unknown_deps(self) -> None:
        cyclic_prd = {
            "project": "Demo",
            "branchName": "floating-loop/demo",
            "description": "Cycle test",
            "verifyCommand": "pytest",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "A",
                    "description": "Desc A",
                    "acceptanceCriteria": ["Check A passes"],
                    "priority": 1,
                    "dependsOn": ["US-002"],
                    "passes": False,
                },
                {
                    "id": "US-002",
                    "title": "B",
                    "description": "Desc B",
                    "acceptanceCriteria": ["Works correctly"],
                    "priority": 2,
                    "dependsOn": ["US-001", "US-999"],
                    "passes": False,
                },
            ],
        }
        errors, warnings = loop_core.validate_prd(cyclic_prd)
        self.assertTrue(any("cycle" in e.lower() for e in errors), errors)
        self.assertTrue(any("US-999" in e for e in errors), errors)
        self.assertTrue(any("vague" in w.lower() for w in warnings), warnings)

    def test_archive_on_branch_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            prd_path = cwd / "prd.json"
            prog_path = cwd / "progress.txt"
            prog_path.write_text("# Old progress\nLearned X\n", encoding="utf-8")

            prd_path.write_text(
                json.dumps({"project": "P", "branchName": "floating-loop/feat-a", "description": "", "userStories": []}),
                encoding="utf-8",
            )
            first = loop_core.archive_previous_run_if_branch_changed(prd_path, prog_path, cwd)
            self.assertIsNone(first)

            # Switch branchName -> must archive old progress.txt and reset progress.txt
            prd_path.write_text(
                json.dumps({"project": "P", "branchName": "floating-loop/feat-b", "description": "", "userStories": []}),
                encoding="utf-8",
            )
            dest = loop_core.archive_previous_run_if_branch_changed(prd_path, prog_path, cwd)
            self.assertIsNotNone(dest)
            assert dest is not None
            self.assertIn("Learned X", (dest / "progress.txt").read_text(encoding="utf-8"))
            self.assertIn("floating-loop/feat-b", prog_path.read_text(encoding="utf-8"))

    def test_metric_parsing_and_effort_escalation(self) -> None:
        self.assertAlmostEqual(loop_core.parse_last_float("p95=42.5ms\nscore: 18.25"), 18.25)
        self.assertIsNone(loop_core.parse_last_float("no numbers here"))
        self.assertTrue(loop_core.is_metric_improved(12.0, 15.0, "minimize"))
        self.assertFalse(loop_core.is_metric_improved(15.0, 15.0, "minimize"))
        self.assertTrue(loop_core.is_metric_improved(0.95, 0.90, "maximize"))

        self.assertEqual(loop_core.escalate_effort("low"), "medium")
        self.assertEqual(loop_core.escalate_effort("medium"), "high")
        self.assertEqual(loop_core.escalate_effort("high"), "xhigh")
        self.assertEqual(loop_core.escalate_effort("xhigh"), "xhigh")

    def test_floating_swarm_workflow_compiles_with_top_level_await(self) -> None:
        source = SWARM_WORKFLOW.read_text(encoding="utf-8")
        code_obj = compile(source, str(SWARM_WORKFLOW), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        self.assertIsNotNone(code_obj)

    def test_prd_schema_file_is_valid_json(self) -> None:
        schema = json.loads((REPO_ROOT / "schemas" / "prd.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["type"], "object")
        self.assertIn("userStories", schema["required"])
        story_props = schema["properties"]["userStories"]["items"]["properties"]
        for expected in ("dependsOn", "verifyCommand", "hat", "model", "effort", "filesHint"):
            self.assertIn(expected, story_props)

    def test_validate_prd_edge_cases_and_strict_warnings(self) -> None:
        bad_prd = {
            "project": "Edge",
            "branchName": "invalid branch with spaces",
            "description": "Edge cases",
            "defaultEffort": "ultra",
            "maxRetriesPerStory": -1,
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Self dep",
                    "description": "Self dep",
                    "acceptanceCriteria": ["Works correctly"],
                    "priority": 0,
                    "dependsOn": ["US-001"],
                    "hat": "wizard",
                    "effort": "turbo",
                    "passes": "no",
                },
                {
                    "id": "US-001",
                    "title": "Dup",
                    "description": "Dup",
                    "acceptanceCriteria": ["Valid check"],
                    "priority": 1,
                    "passes": False,
                },
            ],
        }
        errors, warnings = loop_core.validate_prd(bad_prd)
        joined = "\n".join(errors)
        self.assertIn("branchName", joined)
        self.assertIn("defaultEffort", joined)
        self.assertIn("maxRetriesPerStory", joined)
        self.assertIn("cannot depend on itself", joined)
        self.assertIn("duplicate story id", joined)
        self.assertIn("invalid hat", joined)
        self.assertIn("invalid effort", joined)
        self.assertIn("'priority' must be an integer >= 1", joined)
        self.assertIn("'passes' must be a boolean", joined)
        self.assertTrue(any("verifyCommand" in w for w in warnings))

        # --strict fails when warnings exist even with 0 errors
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "warn-only.json"
            p.write_text(
                json.dumps(
                    {
                        "project": "WarnOnly",
                        "branchName": "floating-loop/warn",
                        "description": "No verifyCommand",
                        "userStories": [
                            {
                                "id": "US-001",
                                "title": "Simple",
                                "description": "Simple",
                                "acceptanceCriteria": ["File created"],
                                "priority": 1,
                                "passes": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            r_non_strict = subprocess.run([sys.executable, str(LOOP_CTL), "validate-prd", str(p)], capture_output=True, text=True)
            self.assertEqual(r_non_strict.returncode, 0)
            r_strict = subprocess.run([sys.executable, str(LOOP_CTL), "validate-prd", str(p), "--strict"], capture_output=True, text=True)
            self.assertEqual(r_strict.returncode, 1)

    def test_cli_lifecycle_status_cancel_mark_and_eval_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            # Start 2 separate sessions
            for cid in ("s1", "s2"):
                r = subprocess.run(
                    [sys.executable, str(LOOP_CTL), "start", "--cwd", str(ws), "--conversation-id", cid, "--prompt", f"Task {cid}"],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(r.returncode, 0, r.stderr)

            # status --json returns both
            st = subprocess.run(
                [sys.executable, str(LOOP_CTL), "status", "--cwd", str(ws), "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(st.returncode, 0)
            self.assertEqual(len(json.loads(st.stdout)["sessions"]), 2)

            # Ambiguous cancel without --conversation-id or --all must fail
            amb = subprocess.run([sys.executable, str(LOOP_CTL), "cancel", "--cwd", str(ws)], capture_output=True, text=True)
            self.assertEqual(amb.returncode, 1)

            # Cancel specific id, then auto-cancel remaining single session
            c1 = subprocess.run(
                [sys.executable, str(LOOP_CTL), "cancel", "--cwd", str(ws), "--conversation-id", "s1"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(c1.returncode, 0)
            c2 = subprocess.run([sys.executable, str(LOOP_CTL), "cancel", "--cwd", str(ws)], capture_output=True, text=True)
            self.assertEqual(c2.returncode, 0)

            st_after = subprocess.run(
                [sys.executable, str(LOOP_CTL), "status", "--cwd", str(ws), "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(st_after.stdout)["sessions"], [])

            # mark-story + next-story / next-wave round-trip
            prd_copy = ws / "prd.json"
            prd_copy.write_text(EXAMPLE_PRD.read_text(encoding="utf-8"), encoding="utf-8")
            m = subprocess.run(
                [sys.executable, str(LOOP_CTL), "mark-story", str(prd_copy), "--id", "US-001", "--passes", "true", "--note", "done"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(m.returncode, 0)
            nw = subprocess.run(
                [sys.executable, str(LOOP_CTL), "next-wave", str(prd_copy), "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(nw.returncode, 0)
            wave_ids = [s["id"] for s in json.loads(nw.stdout)["readyWave"]]
            self.assertEqual(wave_ids, ["US-002", "US-003"])

            # eval-metric CLI
            ev = subprocess.run(
                [sys.executable, str(LOOP_CTL), "eval-metric", "--output", "acc=0.94", "--best", "0.90", "--direction", "maximize", "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(ev.returncode, 0)
            ev_data = json.loads(ev.stdout)
            self.assertTrue(ev_data["ok"])
            self.assertTrue(ev_data["improved"])
            self.assertAlmostEqual(ev_data["value"], 0.94)

            ev_bad = subprocess.run(
                [sys.executable, str(LOOP_CTL), "eval-metric", "--output", "nan-only", "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(ev_bad.returncode, 1)

    def test_takeover_claude_ralph_loop_mid_run_and_stop_hook_continues(self) -> None:
        stop_hook = SCRIPTS_DIR / "stop-hook.py"
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            claude_dir = ws / ".claude"
            claude_dir.mkdir()
            legacy_file = claude_dir / "ralph-loop.local.md"
            legacy_file.write_text(
                "---\n"
                "active: true\n"
                "iteration: 7\n"
                "session_id: claude-old-123\n"
                "max_iterations: 20\n"
                'completion_promise: "ALL TESTS GREEN"\n'
                'started_at: "2026-11-20T10:00:00Z"\n'
                "---\n\n"
                "Finish refactoring auth middleware.\n---\nEnsure 100% pass rate.\n",
                encoding="utf-8",
            )

            res = subprocess.run(
                [
                    sys.executable,
                    str(LOOP_CTL),
                    "takeover",
                    "--cwd",
                    str(ws),
                    "--conversation-id",
                    "agy-99",
                    "--verify-cmd",
                    "test -f done.ok",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(res.stdout)["results"][0]
            self.assertEqual(payload["kind"], "in-session")
            self.assertEqual(payload["iteration"], 7)
            self.assertEqual(payload["maxIterations"], 20)
            self.assertEqual(payload["completionPromise"], "ALL TESTS GREEN")
            self.assertFalse(legacy_file.exists())
            self.assertTrue(Path(payload["archivedSource"]).is_file())

            st = json.loads((ws / ".floating-loop" / "sessions" / "agy-99.json").read_text(encoding="utf-8"))
            self.assertIn("---\nEnsure 100% pass rate.", st["prompt"])
            self.assertEqual(st["takenOverFrom"]["sourceSessionId"], "claude-old-123")

            # Now fire stop-hook.py -> must continue directly at iteration 8/20!
            (ws / "auth.py").write_text("refactored = True\n", encoding="utf-8")
            transcript = Path(tmp) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "working"}) + "\n",
                encoding="utf-8",
            )
            hook_in = {
                "conversationId": "agy-99",
                "cwd": str(ws),
                "workspacePaths": [str(ws)],
                "transcriptPath": str(transcript),
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
            }
            h = subprocess.run(
                [sys.executable, str(stop_hook)],
                input=json.dumps(hook_in),
                capture_output=True,
                text=True,
            )
            self.assertEqual(h.returncode, 0, h.stderr)
            out = json.loads(h.stdout)
            self.assertEqual(out["decision"], "continue")
            self.assertIn("Iteration 8/20", out["reason"])
            self.assertIn("ALL TESTS GREEN", out["reason"])

            # Regression check: taking over a multi-day-old loop WITHOUT --conversation-id
            # creates pending.json and MUST still be claimed by Stop hook (TTL uses updatedAt).
            legacy_file.write_text(
                "---\nactive: true\niteration: 4\nmax_iterations: 9\ncompletion_promise: \"OK\"\n"
                'started_at: "2026-01-01T00:00:00Z"\n---\n\nOld loop prompt.\n',
                encoding="utf-8",
            )
            r_old = subprocess.run(
                [sys.executable, str(LOOP_CTL), "takeover", "--cwd", str(ws), "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r_old.returncode, 0, r_old.stderr)
            h_old = subprocess.run(
                [sys.executable, str(stop_hook)],
                input=json.dumps({**hook_in, "conversationId": "agy-claimed-old"}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(h_old.returncode, 0, h_old.stderr)
            out_old = json.loads(h_old.stdout)
            self.assertEqual(out_old["decision"], "continue")
            self.assertIn("Iteration 5/9", out_old["reason"])

    def test_takeover_snarktank_ralph_prd_mid_run_and_ambiguous_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "repo"
            ws.mkdir()
            subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "ralph/task-priority"], check=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@local"], check=True)
            subprocess.run(["git", "-C", str(ws), "config", "user.name", "T"], check=True)

            legacy_prd = {
                "project": "LegacyApp",
                "branchName": "ralph/task-priority",
                "description": "Mid-run snarktank/ralph PRD",
                "userStories": [
                    {
                        "id": "US-001",
                        "title": "Done story",
                        "description": "Already done",
                        "acceptanceCriteria": ["Done"],
                        "priority": 1,
                        "passes": True,
                        "notes": "",
                    },
                    {
                        "id": "US-002",
                        "title": "Next story",
                        "description": "In progress",
                        "acceptanceCriteria": ["Tests pass"],
                        "priority": 2,
                        "passes": False,
                        "notes": "",
                    },
                    {
                        "id": "US-003",
                        "title": "Final story",
                        "description": "After US-002",
                        "acceptanceCriteria": ["Tests pass"],
                        "priority": 3,
                        "passes": False,
                        "notes": "",
                    },
                ],
            }
            (ws / "prd.json").write_text(json.dumps(legacy_prd, indent=2), encoding="utf-8")
            (ws / "progress.txt").write_text("# Ralph Progress\n## Codebase Patterns\n- Keep foo pure\n", encoding="utf-8")
            (ws / ".last-branch").write_text("ralph/task-priority\n", encoding="utf-8")
            (ws / "CLAUDE.md").write_text("# Repo conventions\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(ws), "add", "prd.json", "CLAUDE.md"], check=True)
            subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "feat: US-001"], check=True)
            # Simulate interrupted half-finished edit
            (ws / "draft.py").write_text("partial = 1\n", encoding="utf-8")

            # Also add a .claude/ralph-loop.local.md to test ambiguity detection first
            (ws / ".claude").mkdir()
            (ws / ".claude" / "ralph-loop.local.md").write_text(
                "---\nactive: true\niteration: 2\nmax_iterations: 5\ncompletion_promise: null\n---\n\nPrompt\n",
                encoding="utf-8",
            )
            amb = subprocess.run([sys.executable, str(LOOP_CTL), "takeover", "--cwd", str(ws)], capture_output=True, text=True)
            self.assertEqual(amb.returncode, 1)
            self.assertIn("Multiple upstream Ralph state sources", amb.stderr)

            # Take over PRD explicitly with --add-sequential-deps and --verify-cmd
            res = subprocess.run(
                [
                    sys.executable,
                    str(LOOP_CTL),
                    "takeover",
                    "--cwd",
                    str(ws),
                    "--kind",
                    "prd",
                    "--add-sequential-deps",
                    "--verify-cmd",
                    "pytest -q",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            info = json.loads(res.stdout)["results"][0]
            self.assertEqual(info["passedCount"], 1)
            self.assertEqual(info["nextStory"], "US-002")
            self.assertTrue(info["branchMatches"])
            self.assertTrue(info["progressTxtExists"])
            self.assertIn("CLAUDE.md", info["claudeMdFiles"])
            self.assertTrue(any("draft.py" in line for line in info["dirtyFiles"]))
            self.assertIn("Keep foo pure", (ws / "progress.txt").read_text(encoding="utf-8"))

            updated_prd = json.loads((ws / "prd.json").read_text(encoding="utf-8"))
            self.assertEqual(updated_prd["verifyCommand"], "pytest -q")
            self.assertEqual(updated_prd["userStories"][0]["dependsOn"], [])
            self.assertEqual(updated_prd["userStories"][1]["dependsOn"], ["US-001"])
            self.assertEqual(updated_prd["userStories"][2]["dependsOn"], ["US-002"])


if __name__ == "__main__":
    unittest.main()
