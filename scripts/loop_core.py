#!/usr/bin/env python3
"""Shared control-plane primitives for Floating Loop (zero external dependencies)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATE_DIR_NAME = ".floating-loop"
SESSIONS_DIR_NAME = "sessions"
ARCHIVE_DIR_NAME = "archive"
PENDING_SESSION_FILE = "pending.json"
LAST_BRANCH_FILE = ".last-branch"

VALID_HATS = {"architect", "implementer", "tester", "verifier", "refactorer", "ui"}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]

VAGUE_CRITERIA_PATTERNS = [
    re.compile(r"^\s*works?\s+(correctly|well|properly|as expected)\s*\.?\s*$", re.I),
    re.compile(r"^\s*good\s+(ux|ui|performance|code)\s*\.?\s*$", re.I),
    re.compile(r"^\s*handles?\s+edge\s+cases?\s*\.?\s*$", re.I),
    re.compile(r"^\s*looks?\s+good\s*\.?\s*$", re.I),
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(ts: str) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return dt.datetime.fromisoformat(ts)
    except ValueError:
        return None


def sanitize_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", value.strip())
    return cleaned or "unknown"


def slugify(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return s or "run"


def state_root(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / STATE_DIR_NAME


def sessions_dir(cwd: str | Path) -> Path:
    return state_root(cwd) / SESSIONS_DIR_NAME


def archive_dir(cwd: str | Path) -> Path:
    return state_root(cwd) / ARCHIVE_DIR_NAME


def session_path(cwd: str | Path, conversation_id: str) -> Path:
    return sessions_dir(cwd) / f"{sanitize_id(conversation_id)}.json"


def pending_session_path(cwd: str | Path) -> Path:
    return sessions_dir(cwd) / PENDING_SESSION_FILE


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def resolve_session_for_hook(
    cwd: str | Path,
    conversation_id: str,
    claim_pending: bool = True,
    pending_ttl_minutes: int = 60,
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Locate session state for conversationId, optionally claiming a fresh pending.json."""
    if conversation_id:
        exact = session_path(cwd, conversation_id)
        data = load_json_file(exact)
        if data is not None:
            return exact, data

    if claim_pending and conversation_id:
        pend_path = pending_session_path(cwd)
        pend = load_json_file(pend_path)
        if pend is not None and pend.get("status") == "active" and not pend.get("conversationId"):
            # Use updatedAt (set on start/takeover) so taking over hours-old Ralph loops still claims cleanly.
            created = parse_iso_utc(str(pend.get("updatedAt") or pend.get("startedAt") or ""))
            now = dt.datetime.now(dt.timezone.utc)
            if created is None or (now - created).total_seconds() <= pending_ttl_minutes * 60:
                pend["conversationId"] = conversation_id
                pend["updatedAt"] = utc_now_iso()
                target = session_path(cwd, conversation_id)
                atomic_write_json(target, pend)
                try:
                    pend_path.unlink()
                except OSError:
                    pass
                return target, pend

    return None, None


def archive_session(cwd: str | Path, state: Dict[str, Any], active_path: Optional[Path] = None) -> Path:
    """Persist a finished/canceled session snapshot in .floating-loop/archive/ and remove active file."""
    arch_dir = archive_dir(cwd) / "sessions"
    arch_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cid = sanitize_id(str(state.get("conversationId") or "pending"))
    dest = arch_dir / f"{stamp}-{cid}-{state.get('status', 'done')}.json"
    atomic_write_json(dest, state)
    if active_path is not None and active_path.exists():
        try:
            active_path.unlink()
        except OSError:
            pass
    return dest


def list_sessions(cwd: str | Path) -> List[Tuple[Path, Dict[str, Any]]]:
    sdir = sessions_dir(cwd)
    if not sdir.is_dir():
        return []
    out: List[Tuple[Path, Dict[str, Any]]] = []
    for p in sorted(sdir.glob("*.json")):
        data = load_json_file(p)
        if data is not None:
            out.append((p, data))
    return out


# ---------------------------------------------------------------------------
# Antigravity Transcript Inspection (transcript.jsonl + transcript_full.jsonl)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class TranscriptResult:
    found: bool
    content: str = ""
    step_index: Optional[int] = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    used_full_transcript: bool = False
    error: str = ""

    @property
    def total_context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens


def _lookup_full_transcript_content(transcript_path: Path, step_index: Optional[int]) -> Optional[str]:
    full_path = transcript_path.parent / "transcript_full.jsonl"
    if not full_path.is_file():
        return None
    fallback: Optional[str] = None
    try:
        with full_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "PLANNER_RESPONSE":
                    continue
                if step_index is not None and obj.get("step_index") == step_index:
                    return str(obj.get("content") or "")
                fallback = str(obj.get("content") or "")
    except OSError:
        return None
    return fallback


def read_latest_planner_response(transcript_path_str: str) -> TranscriptResult:
    """Parse Antigravity transcript.jsonl for the latest PLANNER_RESPONSE visible content & tokens."""
    if not transcript_path_str:
        return TranscriptResult(found=False, error="No transcriptPath provided")
    tpath = Path(transcript_path_str)
    if not tpath.is_file():
        return TranscriptResult(found=False, error=f"Transcript file not found: {tpath}")

    latest_planner: Optional[Dict[str, Any]] = None
    latest_input = 0
    latest_cache = 0
    latest_output = 0

    try:
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") == "PLANNER_RESPONSE" and obj.get("source") in (None, "MODEL"):
                    latest_planner = obj
                    if "input_tokens" in obj or "cache_read_tokens" in obj:
                        latest_input = int(obj.get("input_tokens") or 0)
                        latest_cache = int(obj.get("cache_read_tokens") or 0)
                        latest_output = int(obj.get("output_tokens") or 0)
    except OSError as exc:
        return TranscriptResult(found=False, error=f"Failed reading transcript: {exc}")

    if latest_planner is None:
        return TranscriptResult(found=False, error="No PLANNER_RESPONSE entries found in transcript")

    step_index = latest_planner.get("step_index")
    content = str(latest_planner.get("content") or "")
    truncated_fields = latest_planner.get("truncated_fields") or []
    used_full = False

    if isinstance(truncated_fields, list) and "content" in truncated_fields:
        full_content = _lookup_full_transcript_content(tpath, step_index)
        if full_content is not None:
            content = full_content
            used_full = True

    return TranscriptResult(
        found=True,
        content=content,
        step_index=step_index if isinstance(step_index, int) else None,
        input_tokens=latest_input,
        cache_read_tokens=latest_cache,
        output_tokens=latest_output,
        used_full_transcript=used_full,
    )


def normalize_promise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def extract_promise(content: str) -> Optional[str]:
    """Return whitespace-normalized text inside the last <promise>...</promise> tag."""
    if not content:
        return None
    matches = re.findall(r"<promise>(.*?)</promise>", content, flags=re.DOTALL)
    if not matches:
        return None
    return normalize_promise_text(matches[-1])


def promise_matches(content: str, expected: str) -> bool:
    if not expected or expected.lower() == "null":
        return False
    extracted = extract_promise(content)
    if extracted is None:
        return False
    return extracted == normalize_promise_text(expected)


# ---------------------------------------------------------------------------
# Mechanical Backpressure & Workspace Fingerprinting
# ---------------------------------------------------------------------------


def run_verify_command(cmd: str, cwd: str | Path, timeout: int = 90) -> Tuple[bool, int, str]:
    """Run an independent verification command in cwd and return (passed, rc, tail_output)."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = ""
        if proc.stdout:
            combined += proc.stdout
        if proc.stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += proc.stderr
        tail = combined[-4000:].strip()
        return proc.returncode == 0, proc.returncode, tail
    except subprocess.TimeoutExpired as exc:
        out = ""
        if exc.stdout:
            out += exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        if exc.stderr:
            out += "\n" + (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr)
        return False, 124, f"Verification timed out after {timeout}s.\n{out[-2000:].strip()}".strip()
    except OSError as exc:
        return False, 127, f"Failed to execute verify command: {exc}"


def _run_git(cwd: Path, args: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def compute_workspace_fingerprint(cwd: str | Path) -> str:
    """Compute a stable SHA-256 fingerprint of code progress, ignoring .floating-loop/ telemetry."""
    root = Path(cwd).resolve()
    hasher = hashlib.sha256()

    is_git = (_run_git(root, ["rev-parse", "--is-inside-work-tree"]) or "").strip() == "true"
    if is_git:
        head = (_run_git(root, ["rev-parse", "HEAD"]) or "UNBORN").strip()
        hasher.update(f"HEAD:{head}\n".encode("utf-8"))

        pathspec = [
            "--",
            ".",
            f":(exclude){STATE_DIR_NAME}",
            ":(exclude)progress.txt",
            ":(exclude)results.tsv",
        ]
        status = _run_git(root, ["status", "--porcelain=v1", *pathspec]) or ""
        diff_wt = _run_git(root, ["diff", *pathspec]) or ""
        diff_idx = _run_git(root, ["diff", "--cached", *pathspec]) or ""
        hasher.update(b"STATUS:\n")
        hasher.update(status.encode("utf-8", errors="replace"))
        hasher.update(b"\nDIFF_WT:\n")
        hasher.update(diff_wt.encode("utf-8", errors="replace"))
        hasher.update(b"\nDIFF_IDX:\n")
        hasher.update(diff_idx.encode("utf-8", errors="replace"))

        # Include untracked file contents so edits to newly created files count as progress.
        for line in status.splitlines():
            if line.startswith("?? "):
                rel = line[3:].strip().strip('"')
                fpath = root / rel
                if fpath.is_file():
                    try:
                        data = fpath.read_bytes()[:65536]
                        hasher.update(f"\nUNTRACKED:{rel}:{fpath.stat().st_size}:".encode("utf-8"))
                        hasher.update(hashlib.sha256(data).digest())
                    except OSError:
                        pass
        return hasher.hexdigest()

    skip_dirs = {".git", STATE_DIR_NAME, "node_modules", "__pycache__", ".venv", "venv"}
    entries: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for fname in sorted(filenames):
            if fname in {"progress.txt", "results.tsv"}:
                continue
            full = Path(dirpath) / fname
            rel = full.relative_to(root).as_posix()
            try:
                st = full.stat()
                entries.append(f"{rel}:{st.st_size}:{st.st_mtime_ns}")
            except OSError:
                continue
    hasher.update("\n".join(entries).encode("utf-8"))
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# 3-State Circuit Breaker (CLOSED -> HALF_OPEN -> OPEN)
# ---------------------------------------------------------------------------


def default_circuit_breaker(threshold: int = 3) -> Dict[str, Any]:
    return {
        "state": "CLOSED",
        "noProgressThreshold": max(0, int(threshold)),
        "sameErrorThreshold": max(0, int(threshold)),
        "noProgressStreak": 0,
        "sameErrorStreak": 0,
        "lastWorkspaceHash": "",
        "lastErrorHash": "",
        "tripReason": "",
    }


VOLATILE_PATTERNS = (
    # ISO timestamps & wall-clock times (2026-11-23T15:35:01Z, 15:35:01.123)
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<TIME>"),
    # Test runner durations (in 0.043s, 124ms, 1.25 s, 450 us)
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|us|µs|ns|s|sec|secs|seconds)\b", re.I), "<DUR>"),
    # Hex memory addresses (0x7f8a12bc3400)
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "<ADDR>"),
    # Ephemeral temp dirs (/tmp/pytest-of-user/tmpabc123, /var/folders/.../T/xyz)
    (re.compile(r"/(?:tmp|var/folders)/[^\s:'\"]+"), "<TMP>"),
)


def hash_error_output(error_text: str) -> str:
    """Hash verification error text after stripping volatile timings, timestamps, addresses, and temp paths."""
    cleaned = error_text.strip()
    for pat, repl in VOLATILE_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    normalized = re.sub(r"\s+", " ", cleaned)[:2000]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def update_circuit_breaker(
    cb: Dict[str, Any],
    current_workspace_hash: str,
    error_output: str = "",
) -> Dict[str, Any]:
    """Update circuit breaker streaks and state in place, returning the updated dict."""
    no_prog_thresh = int(cb.get("noProgressThreshold", 3))
    same_err_thresh = int(cb.get("sameErrorThreshold", 3))
    last_ws = str(cb.get("lastWorkspaceHash") or "")

    if last_ws and current_workspace_hash == last_ws:
        cb["noProgressStreak"] = int(cb.get("noProgressStreak", 0)) + 1
    else:
        cb["noProgressStreak"] = 0
        cb["lastWorkspaceHash"] = current_workspace_hash

    if error_output.strip():
        err_hash = hash_error_output(error_output)
        if err_hash == str(cb.get("lastErrorHash") or ""):
            cb["sameErrorStreak"] = int(cb.get("sameErrorStreak", 0)) + 1
        else:
            cb["sameErrorStreak"] = 1
            cb["lastErrorHash"] = err_hash
    else:
        cb["sameErrorStreak"] = 0
        cb["lastErrorHash"] = ""

    no_prog = int(cb["noProgressStreak"])
    same_err = int(cb["sameErrorStreak"])

    if no_prog_thresh > 0 and no_prog >= no_prog_thresh:
        cb["state"] = "OPEN"
        cb["tripReason"] = f"No workspace progress for {no_prog} consecutive iteration(s)."
    elif same_err_thresh > 0 and same_err >= same_err_thresh:
        cb["state"] = "OPEN"
        cb["tripReason"] = f"Identical verification error repeated {same_err} consecutive time(s)."
    elif (no_prog_thresh > 1 and no_prog == no_prog_thresh - 1) or (
        same_err_thresh > 1 and same_err == same_err_thresh - 1
    ):
        cb["state"] = "HALF_OPEN"
        cb["tripReason"] = ""
    else:
        cb["state"] = "CLOSED"
        cb["tripReason"] = ""

    return cb


def reset_circuit_breaker(cb: Dict[str, Any], current_workspace_hash: str = "") -> Dict[str, Any]:
    cb["state"] = "CLOSED"
    cb["noProgressStreak"] = 0
    cb["sameErrorStreak"] = 0
    cb["lastErrorHash"] = ""
    cb["tripReason"] = ""
    if current_workspace_hash:
        cb["lastWorkspaceHash"] = current_workspace_hash
    return cb


# ---------------------------------------------------------------------------
# PRD Validation, DAG Wave Scheduler & Archiving
# ---------------------------------------------------------------------------


def validate_prd(prd: Any) -> Tuple[List[str], List[str]]:
    """Validate prd.json structure & DAG integrity. Returns (errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(prd, dict):
        return ["Top-level prd.json value must be a JSON object."], []

    for field in ("project", "branchName"):
        val = prd.get(field)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"Missing or empty required string field '{field}'.")
    if "description" not in prd or not isinstance(prd.get("description"), str):
        errors.append("Missing required string field 'description'.")

    branch = prd.get("branchName")
    if isinstance(branch, str) and branch.strip() and not re.match(r"^[a-zA-Z0-9._/-]+$", branch):
        errors.append(f"Invalid branchName '{branch}' (contains unsupported characters).")

    default_effort = prd.get("defaultEffort")
    if default_effort is not None and default_effort not in VALID_EFFORTS:
        errors.append(f"Invalid defaultEffort '{default_effort}'. Must be one of {sorted(VALID_EFFORTS)}.")

    max_retries = prd.get("maxRetriesPerStory")
    if max_retries is not None and (not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0):
        errors.append("maxRetriesPerStory must be a non-negative integer.")

    stories = prd.get("userStories")
    if not isinstance(stories, list) or len(stories) == 0:
        errors.append("Field 'userStories' must be a non-empty array.")
        return errors, warnings

    if not prd.get("verifyCommand") and not any(isinstance(s, dict) and s.get("verifyCommand") for s in stories):
        warnings.append("No top-level or per-story 'verifyCommand' defined; Dual-Gate harness verification will be skipped.")

    seen_ids: Dict[str, int] = {}
    id_to_deps: Dict[str, List[str]] = {}

    for idx, story in enumerate(stories):
        prefix = f"userStories[{idx}]"
        if not isinstance(story, dict):
            errors.append(f"{prefix}: story entry must be an object.")
            continue

        sid = story.get("id")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(f"{prefix}: missing or empty 'id'.")
            continue
        if not re.match(r"^[A-Z][A-Z0-9_-]*$", sid):
            warnings.append(f"{prefix} ({sid}): id does not follow uppercase convention (e.g., US-001).")
        if sid in seen_ids:
            errors.append(f"{prefix}: duplicate story id '{sid}' (first seen at index {seen_ids[sid]}).")
            continue
        seen_ids[sid] = idx

        for sfield in ("title", "description"):
            sval = story.get(sfield)
            if not isinstance(sval, str) or not sval.strip():
                errors.append(f"{sid}: missing or empty '{sfield}'.")

        criteria = story.get("acceptanceCriteria")
        if not isinstance(criteria, list) or len(criteria) == 0:
            errors.append(f"{sid}: 'acceptanceCriteria' must be a non-empty array of strings.")
        else:
            for c_idx, crit in enumerate(criteria):
                if not isinstance(crit, str) or not crit.strip():
                    errors.append(f"{sid}: acceptanceCriteria[{c_idx}] must be a non-empty string.")
                elif any(pat.match(crit) for pat in VAGUE_CRITERIA_PATTERNS):
                    warnings.append(f"{sid}: vague acceptance criterion '{crit}' — make it objectively verifiable.")

        prio = story.get("priority")
        if not isinstance(prio, int) or isinstance(prio, bool) or prio < 1:
            errors.append(f"{sid}: 'priority' must be an integer >= 1.")

        passes = story.get("passes")
        if not isinstance(passes, bool):
            errors.append(f"{sid}: 'passes' must be a boolean.")

        hat = story.get("hat")
        if hat is not None and hat not in VALID_HATS:
            errors.append(f"{sid}: invalid hat '{hat}'. Must be one of {sorted(VALID_HATS)}.")

        effort = story.get("effort")
        if effort is not None and effort not in VALID_EFFORTS:
            errors.append(f"{sid}: invalid effort '{effort}'. Must be one of {sorted(VALID_EFFORTS)}.")

        deps = story.get("dependsOn", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) and d.strip() for d in deps):
            errors.append(f"{sid}: 'dependsOn' must be an array of non-empty story ID strings.")
            deps = []
        id_to_deps[sid] = deps

    # Validate dependency references and detect cycles (Kahn's algorithm)
    in_degree: Dict[str, int] = {sid: 0 for sid in seen_ids}
    dependents: Dict[str, List[str]] = {sid: [] for sid in seen_ids}

    for sid, deps in id_to_deps.items():
        for dep in deps:
            if dep == sid:
                errors.append(f"{sid}: cannot depend on itself.")
            elif dep not in seen_ids:
                errors.append(f"{sid}: unknown dependency '{dep}' in dependsOn.")
            else:
                in_degree[sid] += 1
                dependents[dep].append(sid)

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        cur = queue.pop(0)
        visited += 1
        for nxt in dependents[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if visited < len(seen_ids):
        cyclic = sorted(sid for sid, deg in in_degree.items() if deg > 0)
        errors.append(f"Dependency cycle detected among stories: {', '.join(cyclic)}.")

    return errors, warnings


def compute_waves(prd: Dict[str, Any]) -> Dict[str, Any]:
    """Compute ready parallel wave, next serial story, and blocked stories from prd.json."""
    stories: List[Dict[str, Any]] = prd.get("userStories") or []
    passed_ids = {s["id"] for s in stories if s.get("passes") is True}

    ready: List[Tuple[int, int, Dict[str, Any]]] = []
    blocked: List[Dict[str, Any]] = []

    for idx, story in enumerate(stories):
        if story.get("passes") is True:
            continue
        deps = story.get("dependsOn") or []
        missing = [d for d in deps if d not in passed_ids]
        if not missing:
            ready.append((int(story.get("priority", 9999)), idx, story))
        else:
            blocked.append({"id": story["id"], "title": story.get("title", ""), "missingDeps": missing})

    ready.sort(key=lambda item: (item[0], item[1]))
    ready_stories = [item[2] for item in ready]
    total = len(stories)
    passed_count = len(passed_ids)

    return {
        "project": prd.get("project", ""),
        "branchName": prd.get("branchName", ""),
        "verifyCommand": prd.get("verifyCommand", ""),
        "defaultModel": prd.get("defaultModel", ""),
        "defaultEffort": prd.get("defaultEffort", ""),
        "total": total,
        "passedCount": passed_count,
        "remainingCount": total - passed_count,
        "allPassed": total > 0 and passed_count == total,
        "readyWave": ready_stories,
        "nextStory": ready_stories[0] if ready_stories else None,
        "blocked": blocked,
    }


def escalate_effort(current: str) -> str:
    """Step up reasoning effort by one tier (e.g., low -> medium -> high -> xhigh)."""
    if not current:
        return "high"
    try:
        idx = EFFORT_ORDER.index(current)
    except ValueError:
        return "high"
    # Cap automatic escalation at 'xhigh' unless already 'max'
    if idx < 3:
        return EFFORT_ORDER[idx + 1]
    return current


def archive_previous_run_if_branch_changed(
    prd_path: Path,
    progress_path: Path,
    cwd: Path,
) -> Optional[Path]:
    """Archive existing prd.json & progress.txt if branchName changed since last tracked run."""
    prd = load_json_file(prd_path)
    if not prd:
        return None
    current_branch = str(prd.get("branchName") or "").strip()
    if not current_branch:
        return None

    last_branch_file = state_root(cwd) / LAST_BRANCH_FILE
    last_branch = ""
    if last_branch_file.is_file():
        last_branch = last_branch_file.read_text(encoding="utf-8").strip()
    else:
        # Seamless handoff from snarktank/ralph root .last-branch without wiping progress.txt
        legacy_last = cwd / LAST_BRANCH_FILE
        if legacy_last.is_file():
            last_branch = legacy_last.read_text(encoding="utf-8").strip()

    archived_to: Optional[Path] = None
    if last_branch and last_branch != current_branch:
        date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        branch_slug = slugify(re.sub(r"^(floating-loop|ralph)/", "", last_branch))
        dest = archive_dir(cwd) / f"{date_str}-{branch_slug}"
        suffix = 1
        while dest.exists():
            dest = archive_dir(cwd) / f"{date_str}-{branch_slug}-{suffix}"
            suffix += 1
        dest.mkdir(parents=True, exist_ok=True)
        if progress_path.is_file():
            shutil.copy2(progress_path, dest / progress_path.name)
        archived_to = dest

        # Reset progress log for new branch
        progress_path.write_text(
            f"# Floating Loop Progress Log\nBranch: {current_branch}\nStarted: {utc_now_iso()}\n\n## Codebase Patterns\n\n---\n",
            encoding="utf-8",
        )

    last_branch_file.parent.mkdir(parents=True, exist_ok=True)
    last_branch_file.write_text(current_branch + "\n", encoding="utf-8")
    return archived_to


# ---------------------------------------------------------------------------
# Mid-Run Takeover (claude-ralph-loop, gemini ralph, snarktank/ralph)
# ---------------------------------------------------------------------------

LEGACY_SESSION_CANDIDATES = (
    Path(".claude") / "ralph-loop.local.md",
    Path(".gemini") / "ralph-loop.local.md",
    Path(".agents") / "floating-loop.local.md",
)


def parse_ralph_markdown_state(path: Path) -> Dict[str, Any]:
    """Parse upstream claude-ralph-loop / gemini ralph markdown state with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} is missing opening YAML frontmatter delimiter '---'.")

    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break
    if closing_idx is None:
        raise ValueError(f"{path} is missing closing YAML frontmatter delimiter '---'.")

    fm: Dict[str, str] = {}
    for line in lines[1:closing_idx]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        fm[key] = val

    prompt = "\n".join(lines[closing_idx + 1 :]).strip()
    if not prompt:
        raise ValueError(f"{path} has no prompt body after YAML frontmatter.")

    try:
        iteration = int(fm.get("iteration", "1"))
    except ValueError as exc:
        raise ValueError(f"Invalid 'iteration' in {path}: {fm.get('iteration')!r}") from exc
    if iteration < 1:
        raise ValueError(f"'iteration' must be >= 1 in {path}, got {iteration}")

    try:
        max_iterations = int(fm.get("max_iterations", "0"))
    except ValueError as exc:
        raise ValueError(f"Invalid 'max_iterations' in {path}: {fm.get('max_iterations')!r}") from exc
    if max_iterations < 0:
        raise ValueError(f"'max_iterations' must be >= 0 in {path}, got {max_iterations}")

    raw_promise = fm.get("completion_promise", "")
    completion_promise = "" if raw_promise.lower() == "null" else raw_promise

    return {
        "active": fm.get("active", "true").lower() != "false",
        "iteration": iteration,
        "maxIterations": max_iterations,
        "completionPromise": completion_promise,
        "sourceSessionId": fm.get("session_id", ""),
        "startedAt": fm.get("started_at", ""),
        "prompt": prompt,
    }


def detect_takeover_sources(cwd: Path) -> Dict[str, Optional[Path]]:
    """Discover active upstream Ralph state files in cwd."""
    in_session: Optional[Path] = None
    for rel in LEGACY_SESSION_CANDIDATES:
        cand = cwd / rel
        if cand.is_file():
            in_session = cand
            break
    prd_cand = cwd / "prd.json"
    return {
        "in_session": in_session,
        "prd": prd_cand if prd_cand.is_file() else None,
    }


def takeover_in_session_ralph(
    cwd: Path,
    source_path: Path,
    conversation_id: str = "",
    verify_cmd: str = "",
    max_iterations_override: Optional[int] = None,
    cb_threshold: int = 3,
    max_context_tokens: int = 180000,
    allow_reset: bool = False,
    keep_source: bool = False,
) -> Dict[str, Any]:
    """Import an in-progress .claude/ralph-loop.local.md into Floating Loop session state."""
    parsed = parse_ralph_markdown_state(source_path)
    now = utc_now_iso()
    ws_hash = compute_workspace_fingerprint(cwd)
    cb = default_circuit_breaker(cb_threshold)
    cb["lastWorkspaceHash"] = ws_hash

    max_iters = parsed["maxIterations"] if max_iterations_override is None else max_iterations_override
    cid = conversation_id.strip()
    target = session_path(cwd, cid) if cid else pending_session_path(cwd)

    state: Dict[str, Any] = {
        "version": 1,
        "status": "active",
        "conversationId": cid,
        "cwd": str(cwd),
        "prompt": parsed["prompt"],
        "iteration": parsed["iteration"],
        "maxIterations": max_iters,
        "completionPromise": parsed["completionPromise"],
        "verifyCmd": verify_cmd,
        "verifyEveryIteration": False,
        "verifyTimeout": 90,
        "maxContextTokens": max_context_tokens,
        "allowReset": allow_reset,
        "circuitBreaker": cb,
        "startedAt": parsed["startedAt"] or now,
        "updatedAt": now,
        "takenOverFrom": {
            "sourcePath": str(source_path),
            "sourceSessionId": parsed["sourceSessionId"],
            "takenOverAt": now,
        },
        "history": [],
    }
    atomic_write_json(target, state)

    archived_source: Optional[Path] = None
    if not keep_source:
        tdir = archive_dir(cwd) / "takeover"
        tdir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived_source = tdir / f"{stamp}-{source_path.name}"
        shutil.copy2(source_path, archived_source)
        source_path.unlink()

    return {
        "kind": "in-session",
        "statePath": str(target),
        "archivedSource": str(archived_source) if archived_source else "",
        "iteration": state["iteration"],
        "maxIterations": state["maxIterations"],
        "completionPromise": state["completionPromise"],
        "verifyCmd": verify_cmd,
        "conversationId": cid,
    }


def takeover_prd_ralph(
    cwd: Path,
    prd_path: Path,
    verify_cmd: str = "",
    add_sequential_deps: bool = False,
) -> Dict[str, Any]:
    """Prepare a mid-run snarktank/ralph prd.json workspace for safe Floating Loop takeover."""
    prd = load_json_file(prd_path)
    if prd is None:
        raise ValueError(f"Unable to load JSON object from {prd_path}")
    errors, warnings = validate_prd(prd)
    if errors:
        raise ValueError("Invalid prd.json:\n" + "\n".join(f"  - {e}" for e in errors))

    stories: List[Dict[str, Any]] = prd.get("userStories") or []
    has_explicit_deps = any("dependsOn" in s for s in stories)
    modified_prd = False
    backup_path: Optional[Path] = None

    if add_sequential_deps or (verify_cmd and not prd.get("verifyCommand")):
        tdir = archive_dir(cwd) / "takeover"
        tdir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = tdir / f"{stamp}-{prd_path.name}.bak"
        shutil.copy2(prd_path, backup_path)

        if add_sequential_deps and not has_explicit_deps:
            ordered = sorted(enumerate(stories), key=lambda item: (int(item[1].get("priority", 9999)), item[0]))
            prev_id: Optional[str] = None
            for _, story in ordered:
                story["dependsOn"] = [prev_id] if prev_id else []
                prev_id = story["id"]
            has_explicit_deps = True
            modified_prd = True

        if verify_cmd and not prd.get("verifyCommand"):
            prd["verifyCommand"] = verify_cmd
            modified_prd = True

        if modified_prd:
            atomic_write_json(prd_path, prd)

    # Seed .floating-loop/.last-branch with current branchName so existing progress.txt is preserved!
    branch_name = str(prd.get("branchName") or "").strip()
    if branch_name:
        lb = state_root(cwd) / LAST_BRANCH_FILE
        lb.parent.mkdir(parents=True, exist_ok=True)
        lb.write_text(branch_name + "\n", encoding="utf-8")

    # Inspect git state & legacy artifacts (dirty files, branch, CLAUDE.md, progress.txt)
    current_git_branch = (_run_git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    dirty_out = (
        _run_git(cwd, ["status", "--porcelain=v1", "--", ".", f":(exclude){STATE_DIR_NAME}", ":(exclude)progress.txt"])
        or ""
    ).strip()
    dirty_files = [line for line in dirty_out.splitlines() if line.strip()]
    claude_md_files = sorted(
        p.relative_to(cwd).as_posix()
        for p in cwd.rglob("CLAUDE.md")
        if ".git" not in p.parts and "references" not in p.parts and "node_modules" not in p.parts
    )
    waves = compute_waves(prd)

    return {
        "kind": "prd",
        "prdPath": str(prd_path),
        "backupPath": str(backup_path) if backup_path else "",
        "modifiedPrd": modified_prd,
        "project": prd.get("project", ""),
        "branchName": branch_name,
        "currentGitBranch": current_git_branch,
        "branchMatches": bool(branch_name and current_git_branch == branch_name),
        "hasExplicitDependsOn": has_explicit_deps,
        "progressTxtExists": (cwd / "progress.txt").is_file(),
        "claudeMdFiles": claude_md_files,
        "dirtyFiles": dirty_files,
        "passedCount": waves["passedCount"],
        "remainingCount": waves["remainingCount"],
        "total": waves["total"],
        "nextStory": waves["nextStory"]["id"] if waves["nextStory"] else None,
        "verifyCommand": prd.get("verifyCommand", "") or verify_cmd,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Autoresearch Metric Parsing & Comparison
# ---------------------------------------------------------------------------


def parse_last_float(text: str) -> Optional[float]:
    """Extract the last numeric (int/float/scientific) value from metric command output."""
    matches = re.findall(r"[-+]?(?:\d+\.\d+|\d+)(?:[eE][-+]?\d+)?", text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def is_metric_improved(new_val: float, best_val: float, direction: str) -> bool:
    if direction == "minimize":
        return new_val < best_val
    if direction == "maximize":
        return new_val > best_val
    raise ValueError(f"Unsupported metric direction '{direction}'. Expected 'minimize' or 'maximize'.")
