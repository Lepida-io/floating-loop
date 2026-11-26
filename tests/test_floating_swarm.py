#!/usr/bin/env python3
"""End-to-end runtime tests for scripts/workflows/floating_swarm.py.

Executes the unmodified top-level-await workflow script inside a mock Antigravity
`run_workflow` runtime backed by real temporary git repositories and real
`git worktree` sandboxes (matching `workspace="share"` semantics).
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SWARM_WORKFLOW = REPO_ROOT / "scripts" / "workflows" / "floating_swarm.py"


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "swarm-test@floating-loop.local")
    git(path, "config", "user.name", "Floating Swarm Test")


def run_swarm_script(
    repo: Path,
    workflow_args: Dict[str, Any],
    handler: Callable[[Dict[str, Any], Path], Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    """Execute floating_swarm.py with mocked Antigravity workflow primitives."""
    logs: List[str] = []
    calls: List[Dict[str, Any]] = []
    wt_root = Path(tempfile.mkdtemp(prefix="fl-swarm-wt-"))
    wt_counter = 0

    def log(*items: Any) -> None:
        logs.append(" ".join(str(x) for x in items))

    def phase(name: str) -> None:
        logs.append(f"PHASE: {name}")

    def agent(
        prompt: str = "",
        schema: Any = None,
        role: str = "",
        type_name: str = "self",
        workspace: str = "inherit",
        model: str = "inherit",
    ):
        async def _run() -> Dict[str, Any]:
            nonlocal wt_counter
            call_meta = {
                "prompt": prompt,
                "schema": schema,
                "role": role,
                "type_name": type_name,
                "workspace": workspace,
                "model": model,
            }
            calls.append(call_meta)

            if workspace == "share":
                wt_counter += 1
                wt_dir = wt_root / f"wt-{wt_counter}"
                git(repo, "worktree", "add", "--detach", str(wt_dir), "HEAD")
                try:
                    return handler(call_meta, wt_dir)
                finally:
                    git(repo, "worktree", "remove", "--force", str(wt_dir), check=False)
            return handler(call_meta, repo)

        return _run()

    async def parallel(items: List[Any], fn: Callable[[Any], Any]) -> List[Any]:
        coros = [fn(item) for item in items]
        return list(await asyncio.gather(*coros))

    source = SWARM_WORKFLOW.read_text(encoding="utf-8")
    code_obj = compile(source, str(SWARM_WORKFLOW), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    env: Dict[str, Any] = {
        "__name__": "__workflow__",
        "WORKSPACES": (str(repo),),
        "args": dict(workflow_args),
        "log": log,
        "phase": phase,
        "agent": agent,
        "parallel": parallel,
    }

    async def _exec() -> Dict[str, Any]:
        coro = eval(code_obj, env, env)
        if coro is not None:
            await coro
        return env["result"]

    try:
        result = asyncio.run(_exec())
    finally:
        subprocess.run(["rm", "-rf", str(wt_root)], check=False)

    return result, logs, calls


def parse_branch_and_base(prompt: str) -> Tuple[str, str]:
    m = re.search(r"git checkout -B ([A-Za-z0-9._/-]+) ([0-9a-f]+)", prompt)
    assert m is not None, f"Could not find checkout command in prompt:\n{prompt}"
    return m.group(1), m.group(2)


def parse_story_id(prompt: str) -> str:
    m = re.search(r"story (US-\d+)", prompt)
    assert m is not None, f"Could not find story ID in prompt:\n{prompt}"
    return m.group(1)


class TestFloatingSwarmWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        init_repo(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_and_commit_prd(self, prd: Dict[str, Any]) -> None:
        (self.repo / "prd.json").write_text(json.dumps(prd, indent=2) + "\n", encoding="utf-8")
        git(self.repo, "add", "prd.json")
        git(self.repo, "commit", "-m", "chore: add prd.json")

    def test_dry_run_reports_ready_and_blocked_without_spawning_agents(self) -> None:
        prd = {
            "project": "SwarmDemo",
            "branchName": "floating-loop/swarm-demo",
            "description": "Multi-wave demo",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Base types",
                    "description": "Create base types",
                    "acceptanceCriteria": ["base.txt exists"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                },
                {
                    "id": "US-002",
                    "title": "API layer",
                    "description": "Add api",
                    "acceptanceCriteria": ["api.txt exists"],
                    "priority": 2,
                    "dependsOn": ["US-001"],
                    "passes": False,
                },
            ],
        }
        self._write_and_commit_prd(prd)

        def fail_if_called(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            raise AssertionError("No agents should run during dry_run")

        result, _, calls = run_swarm_script(self.repo, {"dry_run": True}, fail_if_called)
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["readyWave"], ["US-001"])
        self.assertEqual(result["blocked"], [("US-002", ["US-001"])])
        self.assertEqual(calls, [])

    def test_multi_wave_parallel_dag_execution_and_verification(self) -> None:
        prd = {
            "project": "SwarmDemo",
            "branchName": "floating-loop/parallel-waves",
            "description": "3-wave diamond DAG",
            "verifyCommand": "python3 -c 'import pathlib; assert pathlib.Path(\"base.txt\").exists()'",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Create base",
                    "description": "Write base.txt",
                    "acceptanceCriteria": ["base.txt created"],
                    "priority": 1,
                    "dependsOn": [],
                    "model": "flash",
                    "passes": False,
                },
                {
                    "id": "US-002",
                    "title": "Create api",
                    "description": "Write api.txt",
                    "acceptanceCriteria": ["api.txt created"],
                    "priority": 2,
                    "dependsOn": ["US-001"],
                    "model": "flash",
                    "passes": False,
                },
                {
                    "id": "US-003",
                    "title": "Create ui",
                    "description": "Write ui.txt",
                    "acceptanceCriteria": ["ui.txt created"],
                    "priority": 2,
                    "dependsOn": ["US-001"],
                    "model": "flash",
                    "passes": False,
                },
                {
                    "id": "US-004",
                    "title": "Integrate",
                    "description": "Write summary.txt referencing api & ui",
                    "acceptanceCriteria": ["summary.txt created"],
                    "priority": 3,
                    "dependsOn": ["US-002", "US-003"],
                    "passes": False,
                },
            ],
        }
        self._write_and_commit_prd(prd)

        file_map = {
            "US-001": "base.txt",
            "US-002": "api.txt",
            "US-003": "ui.txt",
            "US-004": "summary.txt",
        }

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                self.assertEqual(meta["workspace"], "share")
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                fname = file_map[sid]
                (cwd / fname).write_text(f"implemented {sid}\n", encoding="utf-8")
                git(cwd, "add", fname)
                git(cwd, "commit", "-m", f"feat: {sid}")
                sha = git(cwd, "rev-parse", "HEAD").stdout.strip()
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": sha,
                    "summary": f"Added {fname}",
                    "filesChanged": [fname],
                    "checksRun": ["pytest"],
                    "selfVerified": True,
                    "learnings": f"Pattern from {sid}",
                }

            # Adversarial Verifier
            self.assertEqual(meta["workspace"], "inherit")
            self.assertEqual(meta["model"], "pro")
            return {
                "storyId": sid,
                "approved": True,
                "summary": f"Diff verified for {sid}",
                "findings": [],
                "missingCriteria": [],
            }

        result, logs, calls = run_swarm_script(
            self.repo,
            {"max_waves": 6, "max_concurrency": 4, "worker_model": "flash", "verifier_model": "pro"},
            handler,
        )
        self.assertTrue(result["allPassed"])
        self.assertEqual(result["passed"], 4)
        self.assertEqual(result["mergedInRun"], ["US-001", "US-002", "US-003", "US-004"])
        self.assertTrue(any("Wave 2: Implement (US-002, US-003)" in line for line in logs))
        for fname in file_map.values():
            self.assertTrue((self.repo / fname).is_file())
        self.assertTrue((self.repo / "progress.txt").is_file())
        # Ensure temporary worker branches were pruned
        branches = git(self.repo, "branch", "--list", "floating-loop/worker-*").stdout.strip()
        self.assertEqual(branches, "")
        self.assertEqual(len(calls), 8)  # 4 implementers + 4 adversarial verifiers

    def test_verifier_rejection_triggers_model_escalation_and_retry(self) -> None:
        prd = {
            "project": "EscalateDemo",
            "branchName": "floating-loop/escalate",
            "description": "Rejection -> escalation",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Hard feature",
                    "description": "Needs edge case check",
                    "acceptanceCriteria": ["edge.txt has FIXED"],
                    "priority": 1,
                    "dependsOn": [],
                    "model": "flash",
                    "passes": False,
                }
            ],
        }
        self._write_and_commit_prd(prd)
        attempts = {"US-001": 0}
        worker_models: List[str] = []

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                attempts[sid] += 1
                worker_models.append(meta["model"])
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                if attempts[sid] == 1:
                    (cwd / "edge.txt").write_text("INCOMPLETE\n", encoding="utf-8")
                else:
                    self.assertIn("Missing null guard", meta["prompt"])
                    (cwd / "edge.txt").write_text("FIXED\n", encoding="utf-8")
                git(cwd, "add", "edge.txt")
                git(cwd, "commit", "-m", f"feat: {sid} attempt {attempts[sid]}")
                sha = git(cwd, "rev-parse", "HEAD").stdout.strip()
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": sha,
                    "summary": f"Attempt {attempts[sid]}",
                    "filesChanged": ["edge.txt"],
                    "checksRun": [],
                    "selfVerified": True,
                }

            # Verifier checks actual content on the worker branch!
            if attempts[sid] == 1:
                return {
                    "storyId": sid,
                    "approved": False,
                    "summary": "Missing null guard",
                    "findings": ["Missing null guard in edge.txt"],
                    "missingCriteria": ["edge.txt has FIXED"],
                }
            return {
                "storyId": sid,
                "approved": True,
                "summary": "Looks solid",
                "findings": [],
                "missingCriteria": [],
            }

        result, _, _ = run_swarm_script(
            self.repo,
            {"worker_model": "flash", "escalate_model": "pro", "verifier_model": "pro", "max_retries_per_story": 2},
            handler,
        )
        self.assertTrue(result["allPassed"])
        self.assertEqual(worker_models, ["flash", "pro"])  # Auto-escalated on attempt 2!
        self.assertEqual((self.repo / "edge.txt").read_text(encoding="utf-8").strip(), "FIXED")

    def test_post_merge_verify_failure_reverts_merge_and_retries(self) -> None:
        prd = {
            "project": "RollbackDemo",
            "branchName": "floating-loop/rollback",
            "description": "Post-merge verify failure rollback",
            "verifyCommand": "grep -q GREEN status.txt",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Write GREEN status",
                    "description": "status.txt must contain GREEN",
                    "acceptanceCriteria": ["status.txt has GREEN"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                }
            ],
        }
        self._write_and_commit_prd(prd)
        attempts = {"US-001": 0}

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                attempts[sid] += 1
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                if attempts[sid] == 1:
                    (cwd / "status.txt").write_text("RED\n", encoding="utf-8")
                else:
                    self.assertIn("Post-merge", meta["prompt"])
                    (cwd / "status.txt").write_text("GREEN\n", encoding="utf-8")
                git(cwd, "add", "status.txt")
                git(cwd, "commit", "-m", f"feat: {sid} attempt {attempts[sid]}")
                sha = git(cwd, "rev-parse", "HEAD").stdout.strip()
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": sha,
                    "summary": "Updated status",
                    "filesChanged": ["status.txt"],
                    "checksRun": [],
                    "selfVerified": True,
                }
            return {
                "storyId": sid,
                "approved": True,
                "summary": "Approved",
                "findings": [],
                "missingCriteria": [],
            }

        result, logs, _ = run_swarm_script(self.repo, {"max_retries_per_story": 2}, handler)
        self.assertTrue(result["allPassed"])
        self.assertTrue(any("POST-MERGE VERIFY FAILED" in line for line in logs))
        self.assertEqual((self.repo / "status.txt").read_text(encoding="utf-8").strip(), "GREEN")

    def test_parallel_merge_conflict_recovers_cleanly_in_next_wave(self) -> None:
        (self.repo / "shared.txt").write_text("header\nvalue=0\n", encoding="utf-8")
        git(self.repo, "add", "shared.txt")
        git(self.repo, "commit", "-m", "chore: add shared.txt")

        prd = {
            "project": "ConflictDemo",
            "branchName": "floating-loop/conflict",
            "description": "Two parallel workers touch shared.txt",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Update value to 1",
                    "description": "Modify shared.txt",
                    "acceptanceCriteria": ["shared.txt updated"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                },
                {
                    "id": "US-002",
                    "title": "Append note to shared.txt",
                    "description": "Modify shared.txt too",
                    "acceptanceCriteria": ["shared.txt has note"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                },
            ],
        }
        self._write_and_commit_prd(prd)
        attempts = {"US-001": 0, "US-002": 0}

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                attempts[sid] += 1
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                if sid == "US-001":
                    (cwd / "shared.txt").write_text("header\nvalue=1\n", encoding="utf-8")
                elif attempts[sid] == 1:
                    # Conflicting edit on line 2!
                    (cwd / "shared.txt").write_text("header\nvalue=2-conflict\n", encoding="utf-8")
                else:
                    # Rebased retry on top of US-001: append cleanly!
                    existing = (cwd / "shared.txt").read_text(encoding="utf-8")
                    (cwd / "shared.txt").write_text(existing + "note=ok\n", encoding="utf-8")
                git(cwd, "add", "shared.txt")
                git(cwd, "commit", "-m", f"feat: {sid}")
                sha = git(cwd, "rev-parse", "HEAD").stdout.strip()
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": sha,
                    "summary": f"Edited shared.txt for {sid}",
                    "filesChanged": ["shared.txt"],
                    "checksRun": [],
                    "selfVerified": True,
                }
            return {
                "storyId": sid,
                "approved": True,
                "summary": "OK",
                "findings": [],
                "missingCriteria": [],
            }

        result, logs, _ = run_swarm_script(self.repo, {"max_waves": 4, "max_retries_per_story": 2}, handler)
        self.assertTrue(result["allPassed"])
        self.assertTrue(any("MERGE CONFLICT on US-002" in line for line in logs))
        final_shared = (self.repo / "shared.txt").read_text(encoding="utf-8")
        self.assertIn("value=1", final_shared)
        self.assertIn("note=ok", final_shared)

    def test_exhausted_retries_trip_circuit_breaker_and_halt(self) -> None:
        prd = {
            "project": "ExhaustDemo",
            "branchName": "floating-loop/exhaust",
            "description": "Always rejected story",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Impossible",
                    "description": "Always fails verification",
                    "acceptanceCriteria": ["impossible"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                }
            ],
        }
        self._write_and_commit_prd(prd)

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                (cwd / "x.txt").write_text("attempt\n", encoding="utf-8")
                git(cwd, "add", "x.txt")
                git(cwd, "commit", "--allow-empty", "-m", "feat: attempt")
                sha = git(cwd, "rev-parse", "HEAD").stdout.strip()
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": sha,
                    "summary": "Attempt",
                    "filesChanged": ["x.txt"],
                    "checksRun": [],
                    "selfVerified": False,
                }
            return {
                "storyId": sid,
                "approved": False,
                "summary": "Still broken",
                "findings": ["broken"],
                "missingCriteria": ["impossible"],
            }

        result, logs, _ = run_swarm_script(self.repo, {"max_waves": 5, "max_retries_per_story": 1}, handler)
        self.assertFalse(result["allPassed"])
        self.assertEqual(result["exhausted"], ["US-001"])
        self.assertTrue(any("CIRCUIT BREAKER" in line for line in logs))

    def test_dirty_working_tree_refuses_to_start(self) -> None:
        prd = {
            "project": "DirtyDemo",
            "branchName": "floating-loop/dirty",
            "description": "Dirty tree test",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "A",
                    "description": "A",
                    "acceptanceCriteria": ["A"],
                    "priority": 1,
                    "passes": False,
                }
            ],
        }
        self._write_and_commit_prd(prd)
        (self.repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            run_swarm_script(self.repo, {}, lambda m, c: {})
        self.assertIn("Working tree is dirty", str(ctx.exception))

    def test_legacy_prd_without_depends_on_auto_serializes_in_priority_order(self) -> None:
        prd = {
            "project": "LegacySnarktank",
            "branchName": "ralph/legacy-order",
            "description": "No dependsOn fields at all",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Schema first",
                    "description": "Create schema.txt",
                    "acceptanceCriteria": ["schema.txt exists"],
                    "priority": 1,
                    "passes": False,
                },
                {
                    "id": "US-002",
                    "title": "Service second",
                    "description": "Create service.txt reading schema.txt",
                    "acceptanceCriteria": ["service.txt exists"],
                    "priority": 2,
                    "passes": False,
                },
            ],
        }
        self._write_and_commit_prd(prd)

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                if sid == "US-001":
                    (cwd / "schema.txt").write_text("v1\n", encoding="utf-8")
                    fname = "schema.txt"
                else:
                    # Must see US-001's schema.txt already merged because auto-serialized!
                    self.assertTrue((cwd / "schema.txt").is_file(), "US-002 ran before US-001 merged!")
                    (cwd / "service.txt").write_text("uses schema\n", encoding="utf-8")
                    fname = "service.txt"
                git(cwd, "add", fname)
                git(cwd, "commit", "-m", f"feat: {sid}")
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": git(cwd, "rev-parse", "HEAD").stdout.strip(),
                    "summary": f"Added {fname}",
                    "filesChanged": [fname],
                    "checksRun": [],
                    "selfVerified": True,
                }
            return {
                "storyId": sid,
                "approved": True,
                "summary": "Verified",
                "findings": [],
                "missingCriteria": [],
            }

        result, logs, _ = run_swarm_script(self.repo, {"max_concurrency": 4, "max_waves": 4}, handler)
        self.assertTrue(result["allPassed"])
        self.assertTrue(any("Legacy prd.json detected" in line for line in logs))
        self.assertTrue(any("Wave 1: Implement (US-001)" in line for line in logs))
        self.assertTrue(any("Wave 2: Implement (US-002)" in line for line in logs))

    def test_pre_verifier_rejects_prd_tampering_and_zero_commit_workers(self) -> None:
        prd = {
            "project": "TamperDemo",
            "branchName": "floating-loop/tamper",
            "description": "Worker tries to cheat by editing prd.json or committing nothing",
            "userStories": [
                {
                    "id": "US-001",
                    "title": "Honest work",
                    "description": "Create honest.txt",
                    "acceptanceCriteria": ["honest.txt exists"],
                    "priority": 1,
                    "dependsOn": [],
                    "passes": False,
                }
            ],
        }
        self._write_and_commit_prd(prd)
        attempts = {"US-001": 0}
        verifier_calls = 0

        def handler(meta: Dict[str, Any], cwd: Path) -> Dict[str, Any]:
            nonlocal verifier_calls
            sid = parse_story_id(meta["prompt"])
            if meta["role"].startswith("Implement "):
                attempts[sid] += 1
                wbranch, base_sha = parse_branch_and_base(meta["prompt"])
                git(cwd, "checkout", "-B", wbranch, base_sha)
                if attempts[sid] == 1:
                    # Attempt 1: Worker makes ZERO commits on branch!
                    return {
                        "storyId": sid,
                        "branch": wbranch,
                        "commitSha": base_sha,
                        "summary": "Did nothing",
                        "filesChanged": [],
                        "checksRun": [],
                        "selfVerified": True,
                    }
                if attempts[sid] == 2:
                    # Attempt 2: Worker tampers with prd.json directly!
                    self.assertIn("zero commits", meta["prompt"])
                    p = json.loads((cwd / "prd.json").read_text(encoding="utf-8"))
                    p["userStories"][0]["passes"] = True
                    (cwd / "prd.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
                    git(cwd, "add", "prd.json")
                    git(cwd, "commit", "-m", "cheat: mark self passing")
                    return {
                        "storyId": sid,
                        "branch": wbranch,
                        "commitSha": git(cwd, "rev-parse", "HEAD").stdout.strip(),
                        "summary": "Marked passing",
                        "filesChanged": ["prd.json"],
                        "checksRun": [],
                        "selfVerified": True,
                    }
                # Attempt 3: Honest implementation
                self.assertIn("Forbidden modification to prd.json", meta["prompt"])
                (cwd / "honest.txt").write_text("real work\n", encoding="utf-8")
                git(cwd, "add", "honest.txt")
                git(cwd, "commit", "-m", "feat: US-001")
                return {
                    "storyId": sid,
                    "branch": wbranch,
                    "commitSha": git(cwd, "rev-parse", "HEAD").stdout.strip(),
                    "summary": "Created honest.txt",
                    "filesChanged": ["honest.txt"],
                    "checksRun": [],
                    "selfVerified": True,
                }

            verifier_calls += 1
            return {
                "storyId": sid,
                "approved": True,
                "summary": "Verified honest diff",
                "findings": [],
                "missingCriteria": [],
            }

        result, _, _ = run_swarm_script(self.repo, {"max_retries_per_story": 3, "max_waves": 5}, handler)
        self.assertTrue(result["allPassed"])
        # Verifier should ONLY have been called once (attempt 3), saving pro tokens on attempts 1 & 2!
        self.assertEqual(verifier_calls, 1)
        self.assertTrue((self.repo / "honest.txt").is_file())


if __name__ == "__main__":
    unittest.main()
