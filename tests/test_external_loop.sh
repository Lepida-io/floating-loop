#!/usr/bin/env bash
# End-to-end integration tests for scripts/floating-loop.sh using mocked `agy` binaries
# that enforce Antigravity's real CLI flag contract (--dangerously-skip-permissions ... -p="<prompt>").

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO_ROOT/scripts/floating-loop.sh"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fl-ext-test.XXXXXX")"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

init_git_repo() {
  local dir="$1"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" config user.email "test@floating-loop.local"
  git -C "$dir" config user.name "Floating Loop Test"
}

pass() {
  echo "PASS: $1"
}

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

# Helper sourced by mocks to extract -p=... strictly matching real agy CLI syntax
cat > "$TMP_ROOT/extract-prompt.sh" << 'EOF'
PROMPT_IN=""
FLAG_ARGS=()
for arg in "$@"; do
  case "$arg" in
    -p=*)
      PROMPT_IN="${arg#-p=}"
      ;;
    --print=*)
      PROMPT_IN="${arg#--print=}"
      ;;
    -p|--print)
      echo "Mock agy error: bare -p without =<prompt> is invalid before other flags" >&2
      exit 2
      ;;
    *)
      FLAG_ARGS+=("$arg")
      ;;
  esac
done
if [[ -z "$PROMPT_IN" ]]; then
  echo "Mock agy error: missing -p=<prompt> argument" >&2
  exit 2
fi
EOF

# ---------------------------------------------------------------------------
# Test 1: --dry-run preview on examples/prd.json.example (must be strictly read-only!)
# ---------------------------------------------------------------------------
DRY_REPO="$TMP_ROOT/repo-dry"
mkdir -p "$DRY_REPO"
cp "$REPO_ROOT/examples/prd.json.example" "$DRY_REPO/prd.json"
DRY_OUT="$("$RUNNER" --mode prd --cwd "$DRY_REPO" --prd "$DRY_REPO/prd.json" --dry-run)"
echo "$DRY_OUT" | grep -q "\[DRY-RUN\] Command: agy --dangerously-skip-permissions" || fail "dry-run missing command preview"
echo "$DRY_OUT" | grep -q -- "-p=" || fail "dry-run missing -p= flag syntax"
echo "$DRY_OUT" | grep -q "US-001" || fail "dry-run missing US-001"
echo "$DRY_OUT" | grep -q "Active Hat: Architect" || fail "dry-run missing Architect hat overlay"
[[ ! -e "$DRY_REPO/.floating-loop" ]] || fail "--dry-run must NOT create .floating-loop/"
[[ ! -e "$DRY_REPO/progress.txt" ]] || fail "--dry-run must NOT create progress.txt"
pass "Test 1: --dry-run preview with hat overlay, real -p= syntax, and zero filesystem side effects"

# ---------------------------------------------------------------------------
# Test 2: PRD mode Dual-Gate rejection + automatic effort escalation -> pass
# ---------------------------------------------------------------------------
REPO2="$TMP_ROOT/repo-prd"
init_git_repo "$REPO2"
cat > "$REPO2/prd.json" << 'EOF'
{
  "project": "Demo",
  "branchName": "floating-loop/demo",
  "description": "Demo feature",
  "verifyCommand": "test -f app.txt && grep -q FIXED app.txt",
  "defaultEffort": "medium",
  "userStories": [
    {
      "id": "US-001",
      "title": "Create fixed app file",
      "description": "Write FIXED to app.txt",
      "acceptanceCriteria": ["app.txt contains FIXED"],
      "priority": 1,
      "dependsOn": [],
      "passes": false,
      "notes": ""
    }
  ]
}
EOF
git -C "$REPO2" add prd.json
git -C "$REPO2" commit -q -m "init"

MOCK_AGY2="$TMP_ROOT/mock-agy-prd.sh"
cat > "$MOCK_AGY2" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/prd-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"
echo "\${FLAG_ARGS[*]}" >> "$TMP_ROOT/prd-args.log"

if [[ "\$N" -eq 1 ]]; then
  # Iteration 1: Dishonestly claim passes=true without creating valid app.txt!
  echo "BROKEN" > app.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
  echo "All done! <promise>COMPLETE</promise>"
else
  # Iteration 2: Verify backpressure was injected, then genuinely fix app.txt!
  echo "\$PROMPT_IN" | grep -q "Previous Iteration Verification Failure" || { echo "Missing backpressure!" >&2; exit 9; }
  echo "FIXED" > app.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
  echo "Verified! <promise>COMPLETE</promise>"
fi
EOF
chmod +x "$MOCK_AGY2"

"$RUNNER" --mode prd --cwd "$REPO2" --prd "$REPO2/prd.json" --max-iterations 5 --sleep 0 --agy-bin "$MOCK_AGY2" >/dev/null
[[ "$(cat "$TMP_ROOT/prd-count")" -eq 2 ]] || fail "Expected exactly 2 iterations (reject then pass)"
head -n 1 "$TMP_ROOT/prd-args.log" | grep -q -- "--model gemini-4-neon-high --effort medium" || fail "Expected frontier default gemini-4-neon-high + medium on iter 1"
tail -n 1 "$TMP_ROOT/prd-args.log" | grep -q -- "--model gemini-4-neon-xhigh --effort high" || fail "Expected auto-escalated gemini-4-neon-xhigh + high on iter 2"
grep -q "FIXED" "$REPO2/app.txt" || fail "Expected FIXED in app.txt"
pass "Test 2: PRD Dual-Gate rejection + backpressure + frontier model & effort cascade"

# ---------------------------------------------------------------------------
# Test 3: PRD mode circuit breaker trips on zero-progress stall (exit 2)
# ---------------------------------------------------------------------------
REPO3="$TMP_ROOT/repo-cb"
init_git_repo "$REPO3"
cp "$REPO2/prd.json" "$REPO3/prd.json"
python3 -c 'import json; d=json.load(open("'"$REPO3/prd.json"'")); d["userStories"][0]["passes"]=False; json.dump(d, open("'"$REPO3/prd.json"'","w"))'
git -C "$REPO3" add prd.json
git -C "$REPO3" commit -q -m "init"

MOCK_STALL="$TMP_ROOT/mock-stall.sh"
cat > "$MOCK_STALL" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
echo "Doing nothing..."
EOF
chmod +x "$MOCK_STALL"

CB_RC=0
"$RUNNER" --mode prd --cwd "$REPO3" --prd "$REPO3/prd.json" --max-iterations 6 --cb-threshold 2 --sleep 0 --agy-bin "$MOCK_STALL" >/dev/null 2>&1 || CB_RC=$?
[[ "$CB_RC" -eq 2 ]] || fail "Expected circuit breaker exit code 2, got $CB_RC"
pass "Test 3: PRD circuit breaker opens cleanly on zero-progress stall"

# ---------------------------------------------------------------------------
# Test 4: Autoresearch keep-or-revert loop (keep better, revert worse & verify fail)
# ---------------------------------------------------------------------------
REPO4="$TMP_ROOT/repo-auto"
init_git_repo "$REPO4"
echo "100" > "$REPO4/score.txt"
echo "ok" > "$REPO4/healthy.txt"
git -C "$REPO4" add score.txt healthy.txt
git -C "$REPO4" commit -q -m "baseline score 100"

MOCK_AUTO="$TMP_ROOT/mock-auto.sh"
cat > "$MOCK_AUTO" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/auto-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"

if [[ "\$N" -eq 1 ]]; then
  echo "60" > score.txt
  git add score.txt && git commit -q -m "exp: optimize to 60"
elif [[ "\$N" -eq 2 ]]; then
  echo "85" > score.txt
  git add score.txt && git commit -q -m "exp: bad change 85"
elif [[ "\$N" -eq 3 ]]; then
  echo "20" > score.txt
  rm -f healthy.txt
  git add -A && git commit -q -m "exp: cheat by breaking health"
fi
EOF
chmod +x "$MOCK_AUTO"

"$RUNNER" \
  --mode autoresearch \
  --cwd "$REPO4" \
  --goal "Minimize score.txt while keeping healthy.txt intact" \
  --metric-cmd "cat score.txt" \
  --direction minimize \
  --verify-cmd "test -f healthy.txt" \
  --max-iterations 3 \
  --cb-threshold 5 \
  --sleep 0 \
  --agy-bin "$MOCK_AUTO" >/dev/null

[[ "$(cat "$REPO4/score.txt")" == "60" ]] || fail "Expected final kept score.txt=60, got $(cat "$REPO4/score.txt")"
[[ -f "$REPO4/healthy.txt" ]] || fail "Expected healthy.txt restored after verify revert"
grep -q $'\tkeep\t' "$REPO4/results.tsv" || fail "Expected keep row in results.tsv"
grep -q $'\trevert\t' "$REPO4/results.tsv" || fail "Expected revert row in results.tsv"
grep -q $'\trevert_verify\t' "$REPO4/results.tsv" || fail "Expected revert_verify row in results.tsv"
pass "Test 4: Autoresearch keep-or-revert + verify gate + results.tsv logging"

# ---------------------------------------------------------------------------
# Test 5: Raw --mode prompt Dual-Gate rejection -> backpressure -> pass
# ---------------------------------------------------------------------------
REPO5="$TMP_ROOT/repo-prompt"
init_git_repo "$REPO5"
echo "Create ready.txt with content OK." > "$REPO5/task.md"
git -C "$REPO5" add task.md
git -C "$REPO5" commit -q -m "init"

MOCK_PROMPT="$TMP_ROOT/mock-prompt.sh"
cat > "$MOCK_PROMPT" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/prompt-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"

if [[ "\$N" -eq 1 ]]; then
  echo "WRONG" > ready.txt
  echo "<promise>SHIP_IT</promise>"
else
  echo "\$PROMPT_IN" | grep -q "Previous Dual-Gate Verification Failure" || { echo "Missing prompt backpressure" >&2; exit 8; }
  echo "OK" > ready.txt
  echo "<promise>SHIP_IT</promise>"
fi
EOF
chmod +x "$MOCK_PROMPT"

"$RUNNER" \
  --mode prompt \
  --cwd "$REPO5" \
  --prompt-file "$REPO5/task.md" \
  --completion-promise "SHIP_IT" \
  --verify-cmd "grep -q OK ready.txt" \
  --max-iterations 4 \
  --sleep 0 \
  --agy-bin "$MOCK_PROMPT" >/dev/null

[[ "$(cat "$TMP_ROOT/prompt-count")" -eq 2 ]] || fail "Expected 2 iterations in prompt mode"
grep -q "OK" "$REPO5/ready.txt" || fail "Expected OK in ready.txt"
pass "Test 5: --mode prompt Dual-Gate backpressure and verified completion"

# ---------------------------------------------------------------------------
# Test 6: Model escalation (--escalate-model) vs --no-cascade
# ---------------------------------------------------------------------------
REPO6="$TMP_ROOT/repo-cascade"
init_git_repo "$REPO6"
cp "$REPO2/prd.json" "$REPO6/prd.json"
python3 -c 'import json; d=json.load(open("'"$REPO6/prd.json"'")); d["userStories"][0]["passes"]=False; json.dump(d, open("'"$REPO6/prd.json"'","w"))'
git -C "$REPO6" add prd.json
git -C "$REPO6" commit -q -m "init"

MOCK_CASC="$TMP_ROOT/mock-casc.sh"
cat > "$MOCK_CASC" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/casc-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"
echo "\${FLAG_ARGS[*]}" >> "$TMP_ROOT/casc-args.log"
if [[ "\$N" -eq 1 ]]; then
  echo "BAD" > app.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
else
  echo "FIXED" > app.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
fi
EOF
chmod +x "$MOCK_CASC"

"$RUNNER" \
  --mode prd \
  --cwd "$REPO6" \
  --prd "$REPO6/prd.json" \
  --model flash \
  --effort low \
  --escalate-model pro \
  --escalate-effort xhigh \
  --max-iterations 4 \
  --sleep 0 \
  --agy-bin "$MOCK_CASC" >/dev/null

head -n 1 "$TMP_ROOT/casc-args.log" | grep -q -- "--model flash --effort low" || fail "Expected flash/low on iter 1"
tail -n 1 "$TMP_ROOT/casc-args.log" | grep -q -- "--model pro --effort xhigh" || fail "Expected pro/xhigh on retry"

rm -f "$TMP_ROOT/casc-count" "$TMP_ROOT/casc-args.log" "$REPO6/app.txt"
python3 -c 'import json; d=json.load(open("'"$REPO6/prd.json"'")); d["userStories"][0]["passes"]=False; json.dump(d, open("'"$REPO6/prd.json"'","w"))'
"$RUNNER" \
  --mode prd \
  --cwd "$REPO6" \
  --prd "$REPO6/prd.json" \
  --model flash \
  --effort low \
  --escalate-model pro \
  --no-cascade \
  --max-iterations 4 \
  --sleep 0 \
  --agy-bin "$MOCK_CASC" >/dev/null

! grep -q -- "--model pro" "$TMP_ROOT/casc-args.log" || fail "--no-cascade should NOT escalate model"
pass "Test 6: Model/effort escalation cascade and --no-cascade override"

# ---------------------------------------------------------------------------
# Test 7: Multi-story DAG ordering (US-001 -> US-002) + branch-change archiving
# ---------------------------------------------------------------------------
REPO7="$TMP_ROOT/repo-dag"
init_git_repo "$REPO7"
mkdir -p "$REPO7/.floating-loop"
echo "floating-loop/old-feature" > "$REPO7/.floating-loop/.last-branch"
echo "# Old progress" > "$REPO7/progress.txt"
cat > "$REPO7/prd.json" << 'EOF'
{
  "project": "DAGDemo",
  "branchName": "floating-loop/new-feature",
  "description": "Two ordered stories",
  "verifyCommand": "test -f step1.txt",
  "userStories": [
    {
      "id": "US-002",
      "title": "Second story depends on US-001",
      "description": "Needs step1.txt first",
      "acceptanceCriteria": ["step2.txt created"],
      "priority": 1,
      "dependsOn": ["US-001"],
      "passes": false
    },
    {
      "id": "US-001",
      "title": "First story",
      "description": "Create step1.txt",
      "acceptanceCriteria": ["step1.txt created"],
      "priority": 2,
      "dependsOn": [],
      "passes": false
    }
  ]
}
EOF
git -C "$REPO7" add prd.json
git -C "$REPO7" commit -q -m "init"

MOCK_DAG="$TMP_ROOT/mock-dag.sh"
cat > "$MOCK_DAG" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
if echo "\$PROMPT_IN" | grep -q '"id": "US-001"'; then
  echo "US-001" >> "$TMP_ROOT/dag-order.log"
  echo "ok" > step1.txt
  python3 -c 'import json; d=json.load(open("prd.json")); [s.__setitem__("passes", True) for s in d["userStories"] if s["id"]=="US-001"]; json.dump(d, open("prd.json","w"))'
elif echo "\$PROMPT_IN" | grep -q '"id": "US-002"'; then
  echo "US-002" >> "$TMP_ROOT/dag-order.log"
  echo "ok" > step2.txt
  python3 -c 'import json; d=json.load(open("prd.json")); [s.__setitem__("passes", True) for s in d["userStories"] if s["id"]=="US-002"]; json.dump(d, open("prd.json","w"))'
  echo "<promise>COMPLETE</promise>"
fi
EOF
chmod +x "$MOCK_DAG"

"$RUNNER" --mode prd --cwd "$REPO7" --prd "$REPO7/prd.json" --max-iterations 5 --sleep 0 --agy-bin "$MOCK_DAG" >/dev/null
[[ "$(tr '\n' ',' < "$TMP_ROOT/dag-order.log")" == "US-001,US-002," ]] || fail "Expected DAG order US-001 then US-002"
find "$REPO7/.floating-loop/archive" -name "progress.txt" | grep -q "old-feature" || fail "Expected archived old-feature progress.txt"
pass "Test 7: Multi-story DAG ordering (ignores higher priority when blocked) + branch archiving"

# ---------------------------------------------------------------------------
# Test 8: Autoresearch maximize + dirty auto-commit + circuit breaker early halt
# ---------------------------------------------------------------------------
REPO8="$TMP_ROOT/repo-auto-max"
init_git_repo "$REPO8"
echo "10" > "$REPO8/acc.txt"
git -C "$REPO8" add acc.txt
git -C "$REPO8" commit -q -m "baseline acc 10"

MOCK_MAX="$TMP_ROOT/mock-auto-max.sh"
cat > "$MOCK_MAX" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/max-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"
if [[ "\$N" -eq 1 ]]; then
  echo "25" > acc.txt
else
  echo "15" > acc.txt
fi
EOF
chmod +x "$MOCK_MAX"

"$RUNNER" \
  --mode autoresearch \
  --cwd "$REPO8" \
  --metric-cmd "cat acc.txt" \
  --direction maximize \
  --max-iterations 6 \
  --cb-threshold 2 \
  --sleep 0 \
  --agy-bin "$MOCK_MAX" >/dev/null

[[ "$(cat "$REPO8/acc.txt")" == "25" ]] || fail "Expected kept maximized acc.txt=25"
[[ "$(cat "$TMP_ROOT/max-count")" -eq 3 ]] || fail "Expected CB halt after 1 keep + 2 reverts (3 iters), got $(cat "$TMP_ROOT/max-count")"
pass "Test 8: Autoresearch maximize + auto-commit + circuit breaker early halt"

# ---------------------------------------------------------------------------
# Test 9: Mid-run takeover of interrupted snarktank/ralph repo (dirty tree + progress.txt)
# ---------------------------------------------------------------------------
REPO9="$TMP_ROOT/repo-takeover"
init_git_repo "$REPO9"
git -C "$REPO9" checkout -q -b ralph/legacy-feat
cat > "$REPO9/prd.json" << 'EOF'
{
  "project": "LegacyTakeover",
  "branchName": "ralph/legacy-feat",
  "description": "Half-finished snarktank/ralph run",
  "userStories": [
    {
      "id": "US-001",
      "title": "Already finished by old Ralph",
      "description": "Done before takeover",
      "acceptanceCriteria": ["part1.txt exists"],
      "priority": 1,
      "passes": true,
      "notes": ""
    },
    {
      "id": "US-002",
      "title": "Interrupted story",
      "description": "Finish part2.txt with COMPLETE_V2",
      "acceptanceCriteria": ["part2.txt contains COMPLETE_V2"],
      "priority": 2,
      "passes": false,
      "notes": ""
    }
  ]
}
EOF
echo "part1" > "$REPO9/part1.txt"
cat > "$REPO9/progress.txt" << 'EOF'
# Ralph Progress Log
## Codebase Patterns
- PRESERVE_THIS_LEARNING
---
EOF
echo "ralph/legacy-feat" > "$REPO9/.last-branch"
git -C "$REPO9" add prd.json part1.txt
git -C "$REPO9" commit -q -m "feat: US-001"
# Interrupted half-written file left on disk!
echo "HALF_WRITTEN" > "$REPO9/part2.txt"

python3 "$REPO_ROOT/scripts/loop-ctl.py" takeover --cwd "$REPO9" --kind prd >/dev/null

MOCK_TO="$TMP_ROOT/mock-takeover.sh"
cat > "$MOCK_TO" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
echo "\$PROMPT_IN" | grep -q '"id": "US-002"' || { echo "Expected US-002, not US-001" >&2; exit 7; }
echo "\$PROMPT_IN" | grep -q "Interrupted Work Detected on Disk" || { echo "Missing dirty-tree takeover warning" >&2; exit 8; }
echo "\$PROMPT_IN" | grep -q "part2.txt" || { echo "Missing part2.txt in dirty notice" >&2; exit 9; }
echo "COMPLETE_V2" > part2.txt
python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][1]["passes"]=True; json.dump(d, open("prd.json","w"))'
echo "<promise>COMPLETE</promise>"
EOF
chmod +x "$MOCK_TO"

"$RUNNER" \
  --mode prd \
  --cwd "$REPO9" \
  --prd "$REPO9/prd.json" \
  --verify-cmd "grep -q COMPLETE_V2 part2.txt" \
  --max-iterations 3 \
  --sleep 0 \
  --agy-bin "$MOCK_TO" >/dev/null

grep -q "PRESERVE_THIS_LEARNING" "$REPO9/progress.txt" || fail "Expected existing progress.txt learnings preserved on takeover"
grep -q "COMPLETE_V2" "$REPO9/part2.txt" || fail "Expected US-002 completed cleanly"
pass "Test 9: Mid-run snarktank/ralph takeover preserves progress.txt and surfaces interrupted edits"

# ---------------------------------------------------------------------------
# Test 10: PRD anti-tampering (deleted story, non-target flip, corrupted JSON)
# ---------------------------------------------------------------------------
REPO10="$TMP_ROOT/repo-tamper"
init_git_repo "$REPO10"
cat > "$REPO10/prd.json" << 'EOF'
{
  "project": "TamperGuard",
  "branchName": "floating-loop/tamper",
  "description": "Defend prd.json against rogue edits",
  "verifyCommand": "test -f u1.txt",
  "userStories": [
    {
      "id": "US-001",
      "title": "First",
      "description": "Create u1.txt",
      "acceptanceCriteria": ["u1.txt exists"],
      "priority": 1,
      "dependsOn": [],
      "passes": false
    },
    {
      "id": "US-002",
      "title": "Second",
      "description": "Create u2.txt",
      "acceptanceCriteria": ["u2.txt exists"],
      "priority": 2,
      "dependsOn": ["US-001"],
      "passes": false
    }
  ]
}
EOF
git -C "$REPO10" add prd.json
git -C "$REPO10" commit -q -m "init"

MOCK_TAMPER="$TMP_ROOT/mock-tamper.sh"
cat > "$MOCK_TAMPER" << EOF
#!/usr/bin/env bash
set -euo pipefail
source "$TMP_ROOT/extract-prompt.sh"
COUNT_FILE="$TMP_ROOT/tamper-count"
N=0
[[ -f "\$COUNT_FILE" ]] && N="\$(cat "\$COUNT_FILE")"
N=\$((N + 1))
echo "\$N" > "\$COUNT_FILE"

if [[ "\$N" -eq 1 ]]; then
  # Attack 1: Delete US-002 from prd.json to claim early completion!
  echo "ok" > u1.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"]=[d["userStories"][0]]; d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
elif [[ "\$N" -eq 2 ]]; then
  # Attack 2: Flip BOTH US-001 and US-002 to passes=true in one turn!
  echo "\$PROMPT_IN" | grep -q "PRD INTEGRITY VIOLATION" || { echo "Missing integrity backpressure 1" >&2; exit 6; }
  python3 -c 'import json; d=json.load(open("prd.json")); [s.__setitem__("passes", True) for s in d["userStories"]]; json.dump(d, open("prd.json","w"))'
elif [[ "\$N" -eq 3 ]]; then
  # Attack 3: Corrupt prd.json with broken JSON!
  echo "\$PROMPT_IN" | grep -q "Non-target story" || { echo "Missing integrity backpressure 2" >&2; exit 7; }
  echo "{broken json" > prd.json
elif [[ "\$N" -eq 4 ]]; then
  # Honest US-001
  echo "\$PROMPT_IN" | grep -q "invalid JSON" || { echo "Missing integrity backpressure 3" >&2; exit 8; }
  echo "ok" > u1.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][0]["passes"]=True; json.dump(d, open("prd.json","w"))'
else
  # Honest US-002
  echo "ok" > u2.txt
  python3 -c 'import json; d=json.load(open("prd.json")); d["userStories"][1]["passes"]=True; json.dump(d, open("prd.json","w"))'
  echo "<promise>COMPLETE</promise>"
fi
EOF
chmod +x "$MOCK_TAMPER"

"$RUNNER" --mode prd --cwd "$REPO10" --prd "$REPO10/prd.json" --max-iterations 6 --cb-threshold 4 --sleep 0 --agy-bin "$MOCK_TAMPER" >/dev/null
[[ "$(cat "$TMP_ROOT/tamper-count")" -eq 5 ]] || fail "Expected 3 rejected tamper attacks + 2 honest stories = 5 turns"
[[ -f "$REPO10/u1.txt" && -f "$REPO10/u2.txt" ]] || fail "Expected both stories completed after tamper recovery"
pass "Test 10: PRD anti-tampering restores deleted stories, non-target flips, and corrupted JSON"

# ---------------------------------------------------------------------------
# Test 11: Autoresearch safety guards (read-only dry-run, dirty-tree refusal, CLI fail CB)
# ---------------------------------------------------------------------------
REPO11="$TMP_ROOT/repo-auto-guard"
init_git_repo "$REPO11"
echo "50" > "$REPO11/m.txt"
git -C "$REPO11" add m.txt
git -C "$REPO11" commit -q -m "baseline"

# 11a: --dry-run must NOT create results.tsv
"$RUNNER" --mode autoresearch --cwd "$REPO11" --metric-cmd "cat m.txt" --dry-run >/dev/null
[[ ! -e "$REPO11/results.tsv" ]] || fail "Autoresearch --dry-run must NOT create results.tsv"

# 11b: Dirty working tree MUST refuse to start (protecting uncommitted user work!)
echo "USER_WIP" > "$REPO11/wip.txt"
DIRTY_RC=0
"$RUNNER" --mode autoresearch --cwd "$REPO11" --metric-cmd "cat m.txt" --agy-bin "$MOCK_STALL" >/dev/null 2>&1 || DIRTY_RC=$?
[[ "$DIRTY_RC" -eq 1 ]] || fail "Autoresearch on dirty tree must fail with exit 1, got $DIRTY_RC"
[[ "$(cat "$REPO11/wip.txt")" == "USER_WIP" ]] || fail "Autoresearch must preserve wip.txt"
rm -f "$REPO11/wip.txt"

# 11c: Consecutive agy CLI failures must trip circuit breaker (exit 2), NOT log bogus results.tsv rows
MOCK_CRASH="$TMP_ROOT/mock-crash.sh"
cat > "$MOCK_CRASH" << 'EOF'
#!/usr/bin/env bash
echo "Auth/model crash" >&2
exit 42
EOF
chmod +x "$MOCK_CRASH"

CRASH_RC=0
"$RUNNER" --mode autoresearch --cwd "$REPO11" --metric-cmd "cat m.txt" --max-iterations 5 --sleep 0 --agy-bin "$MOCK_CRASH" >/dev/null 2>&1 || CRASH_RC=$?
[[ "$CRASH_RC" -eq 2 ]] || fail "Autoresearch CLI crash CB expected exit 2, got $CRASH_RC"
! grep -q "no_change" "$REPO11/results.tsv" || fail "CLI crashes must NOT pollute results.tsv with no_change rows"

# 11d: Prompt mode CLI crash CB also exits 2 cleanly
echo "Do task" > "$REPO11/p.md"
PCRASH_RC=0
"$RUNNER" --mode prompt --cwd "$REPO11" --prompt-file "$REPO11/p.md" --max-iterations 5 --sleep 0 --agy-bin "$MOCK_CRASH" >/dev/null 2>&1 || PCRASH_RC=$?
[[ "$PCRASH_RC" -eq 2 ]] || fail "Prompt mode CLI crash CB expected exit 2, got $PCRASH_RC"
pass "Test 11: Autoresearch read-only dry-run, dirty-tree protection, and CLI crash circuit breakers"

echo "ALL EXTERNAL LOOP INTEGRATION TESTS PASSED!"
