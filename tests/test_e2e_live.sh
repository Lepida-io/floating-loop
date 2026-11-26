#!/usr/bin/env bash
# LIVE End-to-End Test Suite for Floating Loop using the REAL `agy` CLI (no mocks!).
#
# Runs three isolated live tests inside temporary git repositories in /tmp without
# modifying ~/.gemini/config or this repository:
#   E2E 1: Real Antigravity Stop hook + workspace plugin discovery (.agents/plugins/)
#          + claim-pending + real transcript.jsonl + Dual-Gate verifyCmd
#   E2E 2: Real `scripts/floating-loop.sh --mode prd` (2-story DAG + Dual-Gate verify)
#   E2E 3: Real `scripts/floating-loop.sh --mode autoresearch` (O(n^2) -> O(n) keep-or-revert)
#
# Usage:
#   bash tests/test_e2e_live.sh [--model gemini-3.8-flash-low] [--timeout 120s]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO_ROOT/scripts/floating-loop.sh"
LOOP_CTL="$REPO_ROOT/scripts/loop-ctl.py"

MODEL="gemini-4-neon-high"
TIMEOUT="180s"
ONLY="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash tests/test_e2e_live.sh [--model <agy-model>] [--timeout 120s] [--only all|stop|prd|autoresearch|takeover]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

should_run() {
  [[ "$ONLY" == "all" || "$ONLY" == "$1" ]]
}

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fl-e2e-live.XXXXXX")"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

init_repo() {
  local dir="$1"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email "e2e@floating-loop.local"
  git -C "$dir" config user.name "Floating Loop E2E"
}

pass() { echo ""; echo "✅ PASS: $1"; }
fail() { echo ""; echo "❌ FAIL: $1" >&2; exit 1; }

echo "=================================================================="
echo "  Floating Loop LIVE E2E Suite (model=$MODEL, timeout=$TIMEOUT)"
echo "=================================================================="

# ---------------------------------------------------------------------------
# E2E 1: Real Antigravity Stop Hook + Workspace Plugin Discovery + Dual-Gate
# ---------------------------------------------------------------------------
if should_run stop; then
echo ""
echo ">>> E2E 1: Real Antigravity Stop Hook + transcript.jsonl + Dual-Gate"
WS1="$TMP_ROOT/ws-stop"
init_repo "$WS1"

# Install plugin locally inside the temp workspace (.agents/plugins/floating-loop/)
mkdir -p "$WS1/.agents/plugins/floating-loop"
cp "$REPO_ROOT/plugin.json" "$REPO_ROOT/hooks.json" "$WS1/.agents/plugins/floating-loop/"
cp -R "$REPO_ROOT/rules" "$REPO_ROOT/scripts" "$REPO_ROOT/skills" "$REPO_ROOT/assets" "$WS1/.agents/plugins/floating-loop/"

echo "# E2E Stop Hook Test" > "$WS1/README.md"
cat > "$WS1/.gitignore" << 'EOF'
.floating-loop/
.agents/
__pycache__/
EOF
git -C "$WS1" add README.md .gitignore
git -C "$WS1" commit -q -m "init"

# Pre-arm an in-session loop via pending.json (claimed automatically on first Stop hook!)
python3 "$LOOP_CTL" start \
  --cwd "$WS1" \
  --max-iterations 4 \
  --completion-promise "E2E_STOP_OK" \
  --verify-cmd "python3 -c 'import calc; assert calc.add(2, 3) == 5'" \
  --cb-threshold 3 \
  --prompt "Create calc.py in the workspace root defining def add(a, b): return a + b. Test it with python3, then reply with <promise>E2E_STOP_OK</promise>."

# Turn 1 intentionally does NOT create calc.py and does NOT emit <promise>, forcing
# Antigravity's real Stop hook to fire, parse real transcript.jsonl, and continue into Turn 2!
(
  cd "$WS1"
  agy --dangerously-skip-permissions --model "$MODEL" --print-timeout "$TIMEOUT" \
    -p="Create a file named step1.txt containing exactly 'hello' and reply 'STEP1_READY'. Do NOT create calc.py and do NOT output any <promise> tag yet."
)

[[ -f "$WS1/step1.txt" ]] || fail "E2E 1: Turn 1 did not create step1.txt"
[[ -f "$WS1/calc.py" ]] || fail "E2E 1: Stop hook did not continue session to create calc.py"
(cd "$WS1" && python3 -c 'import calc; assert calc.add(2, 3) == 5') || fail "E2E 1: calc.add(2,3) != 5"

# Confirm session completed & archived with iteration >= 2 and real context token telemetry
python3 - "$WS1" << 'PY'
import glob, json, sys
from pathlib import Path
ws = Path(sys.argv[1])
active = list((ws / ".floating-loop" / "sessions").glob("*.json"))
assert len(active) == 0, f"Expected no active session remaining, found {active}"
archived = sorted((ws / ".floating-loop" / "archive" / "sessions").glob("*-completed.json"))
assert len(archived) >= 1, "Expected archived completed session snapshot"
st = json.loads(archived[-1].read_text())
assert st["status"] == "completed", st
assert st["iteration"] >= 2, f"Expected >=2 iterations (proving Stop hook continued), got {st['iteration']}"
assert st.get("conversationId"), "Expected real Antigravity conversationId claimed"
assert st.get("lastContextTokens", 0) > 0, f"Expected real transcript token telemetry > 0, got {st.get('lastContextTokens')}"
print(f"Verified archived session: cid={st['conversationId']}, iterations={st['iteration']}, contextTokens={st['lastContextTokens']}")
PY
pass "E2E 1: Real Antigravity Stop hook continued session, parsed real transcript.jsonl, passed Dual-Gate, and archived state"
fi

# ---------------------------------------------------------------------------
# E2E 2: Real `floating-loop.sh --mode prd` (2-story DAG + Dual-Gate verify)
# ---------------------------------------------------------------------------
if should_run prd; then
echo ""
echo ">>> E2E 2: Real scripts/floating-loop.sh --mode prd (2-story DAG)"
WS2="$TMP_ROOT/ws-prd"
init_repo "$WS2"
cat > "$WS2/.gitignore" << 'EOF'
.floating-loop/
__pycache__/
EOF
cat > "$WS2/prd.json" << 'EOF'
{
  "project": "E2EMath",
  "branchName": "floating-loop/e2e-math",
  "description": "Tiny 2-story Python math module to verify real fresh-context PRD looping",
  "verifyCommand": "python3 -m unittest discover -q",
  "userStories": [
    {
      "id": "US-001",
      "title": "Add mathlib.add and unit test",
      "description": "Create mathlib.py with add(a, b) returning a + b and test_mathlib.py verifying add(2, 3) == 5.",
      "acceptanceCriteria": [
        "mathlib.py defines add(a, b) returning a + b",
        "test_mathlib.py uses unittest to test add(2, 3) == 5",
        "python3 -m unittest discover -q passes"
      ],
      "priority": 1,
      "dependsOn": [],
      "hat": "implementer",
      "passes": false,
      "notes": ""
    },
    {
      "id": "US-002",
      "title": "Add mathlib.mul and unit test",
      "description": "Add mul(a, b) returning a * b to mathlib.py and add a unittest for mul(4, 5) == 20 in test_mathlib.py.",
      "acceptanceCriteria": [
        "mathlib.py defines mul(a, b) returning a * b",
        "test_mathlib.py tests both add(2, 3) == 5 and mul(4, 5) == 20",
        "python3 -m unittest discover -q passes"
      ],
      "priority": 2,
      "dependsOn": ["US-001"],
      "hat": "implementer",
      "passes": false,
      "notes": ""
    }
  ]
}
EOF
git -C "$WS2" add .gitignore prd.json
git -C "$WS2" commit -q -m "init prd"

bash "$RUNNER" \
  --mode prd \
  --cwd "$WS2" \
  --prd "$WS2/prd.json" \
  --model "$MODEL" \
  --no-cascade \
  --print-timeout "$TIMEOUT" \
  --max-iterations 4 \
  --cb-threshold 3 \
  --sleep 0

(cd "$WS2" && python3 -c 'import mathlib; assert mathlib.add(2, 3) == 5 and mathlib.mul(4, 5) == 20') || fail "E2E 2: mathlib assertions failed"
(cd "$WS2" && python3 -m unittest discover -q) || fail "E2E 2: unittest failed"
python3 -c 'import json; d=json.load(open("'"$WS2/prd.json"'")); assert all(s["passes"] for s in d["userStories"]), d' || fail "E2E 2: not all stories marked passing"
[[ -f "$WS2/progress.txt" ]] || fail "E2E 2: progress.txt missing"
pass "E2E 2: Real floating-loop.sh --mode prd completed 2-story DAG with fresh agy contexts"
fi

# ---------------------------------------------------------------------------
# E2E 3: Real `floating-loop.sh --mode autoresearch` (Keep-or-Revert Optimization)
# ---------------------------------------------------------------------------
if should_run autoresearch; then
echo ""
echo ">>> E2E 3: Real scripts/floating-loop.sh --mode autoresearch (Keep-or-Revert)"
WS3="$TMP_ROOT/ws-auto"
init_repo "$WS3"
cat > "$WS3/.gitignore" << 'EOF'
.floating-loop/
results.tsv
__pycache__/
EOF

cat > "$WS3/algo.py" << 'EOF'
"""Find whether a list contains any duplicate value (intentionally slow baseline)."""

def has_duplicate(items):
    # Deliberately O(n^2) nested comparison loop — optimize to O(n) while preserving behavior!
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            if items[i] == items[j]:
                return True
    return False
EOF

cat > "$WS3/test_algo.py" << 'EOF'
import unittest
from algo import has_duplicate

class TestAlgo(unittest.TestCase):
    def test_cases(self):
        self.assertFalse(has_duplicate([]))
        self.assertFalse(has_duplicate([1, 2, 3, 4]))
        self.assertTrue(has_duplicate([1, 2, 3, 1]))
        self.assertTrue(has_duplicate(["a", "b", "b"]))

if __name__ == "__main__":
    unittest.main()
EOF

cat > "$WS3/bench.py" << 'EOF'
"""Deterministic operation counter for algo.has_duplicate."""
from algo import has_duplicate

class Counted:
    ops = 0
    def __init__(self, v):
        self.v = v
    def __hash__(self):
        Counted.ops += 1
        return hash(self.v)
    def __eq__(self, other):
        Counted.ops += 1
        return isinstance(other, Counted) and self.v == other.v

data = [Counted(i) for i in range(200)]
assert has_duplicate(data) is False
print(Counted.ops)
EOF

git -C "$WS3" add .gitignore algo.py test_algo.py bench.py
git -C "$WS3" commit -q -m "baseline O(n^2) algo"

BASELINE_OPS="$(cd "$WS3" && python3 bench.py)"
echo "Baseline ops: $BASELINE_OPS"

bash "$RUNNER" \
  --mode autoresearch \
  --cwd "$WS3" \
  --goal "Minimize deterministic operation count printed by bench.py by optimizing has_duplicate() in algo.py (e.g., use a Python set) without modifying bench.py or test_algo.py." \
  --metric-cmd "python3 bench.py" \
  --direction minimize \
  --verify-cmd "python3 -m unittest test_algo.py" \
  --model "$MODEL" \
  --print-timeout "$TIMEOUT" \
  --max-iterations 2 \
  --cb-threshold 2 \
  --sleep 0

FINAL_OPS="$(cd "$WS3" && python3 bench.py)"
echo "Final ops: $FINAL_OPS (baseline was $BASELINE_OPS)"
python3 -c "assert float('$FINAL_OPS') < float('$BASELINE_OPS') / 10, f'Expected >10x drop: $BASELINE_OPS -> $FINAL_OPS'" || fail "E2E 3: metric did not improve significantly"
(cd "$WS3" && python3 -m unittest test_algo.py -q) || fail "E2E 3: test_algo.py failed"
grep -q $'\tkeep\t' "$WS3/results.tsv" || fail "E2E 3: expected keep row in results.tsv"
echo "results.tsv:"
cat "$WS3/results.tsv"
pass "E2E 3: Real floating-loop.sh --mode autoresearch optimized O(n^2) -> O(n) ($BASELINE_OPS -> $FINAL_OPS ops) and kept verified commit"
fi

# ---------------------------------------------------------------------------
# E2E 4: Real Mid-Run Takeover of Interrupted snarktank/ralph & claude-ralph-loop
# ---------------------------------------------------------------------------
if should_run takeover; then
echo ""
echo ">>> E2E 4A: Real Mid-Run Takeover of claude-ralph-loop (.claude/ralph-loop.local.md)"
WS4A="$TMP_ROOT/ws-takeover-claude"
init_repo "$WS4A"
mkdir -p "$WS4A/.agents/plugins/floating-loop" "$WS4A/.claude"
cp "$REPO_ROOT/plugin.json" "$REPO_ROOT/hooks.json" "$WS4A/.agents/plugins/floating-loop/"
cp -R "$REPO_ROOT/rules" "$REPO_ROOT/scripts" "$REPO_ROOT/skills" "$REPO_ROOT/assets" "$WS4A/.agents/plugins/floating-loop/"
cat > "$WS4A/.gitignore" << 'EOF'
.floating-loop/
.agents/
.claude/
__pycache__/
EOF
echo "# Takeover" > "$WS4A/README.md"
git -C "$WS4A" add .gitignore README.md
git -C "$WS4A" commit -q -m "init"

# Simulate an in-progress Claude Ralph Loop at iteration 3 of 6
cat > "$WS4A/.claude/ralph-loop.local.md" << 'EOF'
---
active: true
iteration: 3
session_id: claude-prev-session
max_iterations: 6
completion_promise: "TAKEOVER_DONE"
started_at: "2026-11-23T10:00:00Z"
---

Create greet.py defining def hello(name): return f"Hello, {name}!" and verify it with python3, then output <promise>TAKEOVER_DONE</promise>.
EOF

python3 "$LOOP_CTL" takeover \
  --cwd "$WS4A" \
  --verify-cmd "python3 -c 'import greet; assert greet.hello(\"World\") == \"Hello, World!\"'"

[[ ! -f "$WS4A/.claude/ralph-loop.local.md" ]] || fail "E2E 4A: old .claude/ralph-loop.local.md should be archived/removed"
(
  cd "$WS4A"
  agy --dangerously-skip-permissions --model "$MODEL" --print-timeout "$TIMEOUT" \
    -p="Create takeover_ack.txt containing 'ack' and reply 'ACK_ONLY'. Do NOT create greet.py and do NOT output any <promise> tag."
)
(cd "$WS4A" && python3 -c 'import greet; assert greet.hello("World") == "Hello, World!"') || fail "E2E 4A: greet.py missing or wrong after takeover continuation"
python3 - "$WS4A" << 'PY'
import json, sys
from pathlib import Path
ws = Path(sys.argv[1])
archived = sorted((ws / ".floating-loop" / "archive" / "sessions").glob("*-completed.json"))
assert archived, "Expected archived completed session after takeover"
st = json.loads(archived[-1].read_text())
assert st["iteration"] >= 4, f"Expected resumed iteration >= 4 (started at 3), got {st['iteration']}"
print(f"Verified Claude takeover session: resumed 3 -> finished at iteration {st['iteration']}")
PY
pass "E2E 4A: Took over .claude/ralph-loop.local.md at iter 3/6 and completed via real Antigravity Stop hook"

echo ""
echo ">>> E2E 4B: Real Mid-Run Takeover of snarktank/ralph (dirty tree + CLAUDE.md + progress.txt)"
WS4B="$TMP_ROOT/ws-takeover-prd"
init_repo "$WS4B"
git -C "$WS4B" checkout -q -b ralph/text-tools
cat > "$WS4B/.gitignore" << 'EOF'
.floating-loop/
__pycache__/
EOF
cat > "$WS4B/CLAUDE.md" << 'EOF'
# Legacy Claude Rules
- Always include a docstring on public functions in text_utils.py.
EOF
cat > "$WS4B/text_utils.py" << 'EOF'
def shout(s: str) -> str:
    """Return uppercase string."""
    return s.upper()
EOF
cat > "$WS4B/test_text_utils.py" << 'EOF'
import unittest
import text_utils

class TestTextUtils(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(text_utils.shout("hi"), "HI")

if __name__ == "__main__":
    unittest.main()
EOF
cat > "$WS4B/prd.json" << 'EOF'
{
  "project": "TextTools",
  "branchName": "ralph/text-tools",
  "description": "Half-finished snarktank/ralph run",
  "userStories": [
    {
      "id": "US-001",
      "title": "Add shout helper",
      "description": "Implement shout(s) in text_utils.py",
      "acceptanceCriteria": ["shout('hi') == 'HI'", "unittest passes"],
      "priority": 1,
      "passes": true,
      "notes": ""
    },
    {
      "id": "US-002",
      "title": "Add reverse_words helper",
      "description": "Finish implementing reverse_words(s) in text_utils.py (reversing word order separated by spaces) and add a unit test in test_text_utils.py.",
      "acceptanceCriteria": [
        "reverse_words('hello world') == 'world hello'",
        "reverse_words has a docstring (per CLAUDE.md)",
        "python3 -m unittest discover -q passes"
      ],
      "priority": 2,
      "passes": false,
      "notes": ""
    }
  ]
}
EOF
cat > "$WS4B/progress.txt" << 'EOF'
# Ralph Progress Log
## Codebase Patterns
- KEEP_EXISTING_SNARKTANK_PATTERN
---
## Previous - US-001
- Implemented shout(s)
---
EOF
echo "ralph/text-tools" > "$WS4B/.last-branch"
git -C "$WS4B" add .gitignore CLAUDE.md text_utils.py test_text_utils.py prd.json
git -C "$WS4B" commit -q -m "feat: US-001 - Add shout helper"

# Simulate Ctrl+C mid-iteration: half-written broken function left uncommitted on disk!
cat >> "$WS4B/text_utils.py" << 'EOF'

def reverse_words(s: str) -> str:
    # TODO: interrupted mid-edit by old Ralph loop!
    return s
EOF

python3 "$LOOP_CTL" takeover \
  --cwd "$WS4B" \
  --kind prd \
  --add-sequential-deps \
  --verify-cmd "python3 -m unittest discover -q"

bash "$RUNNER" \
  --mode prd \
  --cwd "$WS4B" \
  --prd "$WS4B/prd.json" \
  --model "$MODEL" \
  --no-cascade \
  --print-timeout "$TIMEOUT" \
  --max-iterations 3 \
  --sleep 0

(cd "$WS4B" && python3 -c 'import text_utils; assert text_utils.shout("hi") == "HI"; assert text_utils.reverse_words("hello world") == "world hello"; assert text_utils.reverse_words.__doc__') || fail "E2E 4B: text_utils takeover verification failed"
grep -q "KEEP_EXISTING_SNARKTANK_PATTERN" "$WS4B/progress.txt" || fail "E2E 4B: existing progress.txt pattern was lost"
pass "E2E 4B: Took over interrupted snarktank/ralph repo mid-run, finished dirty US-002, honored CLAUDE.md, and preserved progress.txt"
fi

echo ""
echo "=================================================================="
echo "  LIVE ANTIGRAVITY E2E SUITE PASSED (only=$ONLY)!"
echo "=================================================================="
