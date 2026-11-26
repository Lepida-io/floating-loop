#!/usr/bin/env bash
# Floating Loop — External fresh-context Antigravity CLI runner
# Modes:
#   prd          Fresh-context story-by-story PRD loop with Dual-Gate verify & model cascade
#   prompt       Fresh-context prompt loop until <promise>...</promise> (+ optional verify-cmd)
#   autoresearch Scientific keep-or-revert optimization loop driven by a numeric metric
#
# Compatible with macOS bash 3.2+ and Linux bash 4+. Zero jq dependency (uses Python 3).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_CTL="$SCRIPT_DIR/loop-ctl.py"
PROMPTS_DIR="$SCRIPT_DIR/prompts"

MODE="prd"
WORK_DIR="$(pwd)"
PRD_FILE="prd.json"
PROGRESS_FILE="progress.txt"
RESULTS_FILE="results.tsv"
PROMPT_FILE=""
GOAL_TEXT=""
MAX_ITERATIONS=10
COMPLETION_PROMISE="COMPLETE"
VERIFY_CMD=""
METRIC_CMD=""
DIRECTION="minimize"
MODEL="gemini-4-neon-high"
MODEL_EXPLICIT=0
EFFORT=""
ESCALATE_MODEL="gemini-4-neon-xhigh"
ESCALATE_EFFORT=""
NO_CASCADE=0
PRINT_TIMEOUT="0s"
CB_THRESHOLD=3
SLEEP_SEC=2
AGY_BIN="agy"
DRY_RUN=0

usage() {
  cat << 'EOF'
Floating Loop — External Fresh-Context Antigravity Runner

USAGE:
  floating-loop.sh [OPTIONS]

MODES:
  --mode prd|prompt|autoresearch   Execution mode (default: prd)

CORE OPTIONS:
  --cwd <dir>                      Workspace root (default: current directory)
  --prd <path>                     Path to prd.json for prd mode (default: prd.json)
  --prompt-file <path>             Prompt markdown file (required for prompt mode; optional for autoresearch)
  --goal <text>                    Inline optimization goal text (autoresearch mode)
  --max-iterations <n>             Max fresh iterations (default: 10)
  --completion-promise <text>      Promise text inside <promise>...</promise> (default: COMPLETE)
  --verify-cmd <cmd>               Independent Dual-Gate verification command (overrides/supplements prd.json)

AUTORESEARCH OPTIONS:
  --metric-cmd <cmd>               Command printing a numeric score (last float parsed)
  --direction minimize|maximize    Whether lower or higher metric is better (default: minimize)

MODEL ROUTING & SAFETY:
  --model <name>                   Base Antigravity model passed to `agy --model`
                                   (default: gemini-4-neon-high; pass 'default' to inherit CLI setting)
  --effort <level>                 Base effort (low|medium|high|xhigh|max)
  --escalate-model <name>          Model to switch to on failed story retry (default: gemini-4-neon-xhigh)
  --escalate-effort <level>        Effort to switch to on failed story retry (default: auto step-up)
  --no-cascade                     Disable automatic model/effort escalation on retry
  --print-timeout <duration>       Per-iteration timeout for `agy --print-timeout` (default: 0s)
  --cb-threshold <n>               Circuit breaker stall/repeat-error limit (default: 3, 0=disable)
  --sleep <seconds>                Pause between iterations (default: 2)
  --agy-bin <binary>               CLI binary to invoke (default: agy)
  --dry-run                        Preview assembled prompt & CLI command without calling agy
  -h, --help                       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --mode)
      MODE="$2"; shift 2 ;;
    --cwd)
      WORK_DIR="$2"; shift 2 ;;
    --prd)
      PRD_FILE="$2"; shift 2 ;;
    --prompt-file)
      PROMPT_FILE="$2"; shift 2 ;;
    --goal)
      GOAL_TEXT="$2"; shift 2 ;;
    --max-iterations)
      MAX_ITERATIONS="$2"; shift 2 ;;
    --completion-promise)
      COMPLETION_PROMISE="$2"; shift 2 ;;
    --verify-cmd)
      VERIFY_CMD="$2"; shift 2 ;;
    --metric-cmd)
      METRIC_CMD="$2"; shift 2 ;;
    --direction)
      DIRECTION="$2"; shift 2 ;;
    --model)
      MODEL="$2"; MODEL_EXPLICIT=1; shift 2 ;;
    --effort)
      EFFORT="$2"; shift 2 ;;
    --escalate-model)
      ESCALATE_MODEL="$2"; shift 2 ;;
    --escalate-effort)
      ESCALATE_EFFORT="$2"; shift 2 ;;
    --no-cascade)
      NO_CASCADE=1; shift ;;
    --print-timeout)
      PRINT_TIMEOUT="$2"; shift 2 ;;
    --cb-threshold)
      CB_THRESHOLD="$2"; shift 2 ;;
    --sleep)
      SLEEP_SEC="$2"; shift 2 ;;
    --agy-bin)
      AGY_BIN="$2"; shift 2 ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        MAX_ITERATIONS="$1"; shift
      else
        echo "Error: Unknown argument '$1'" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if [[ "$MODE" != "prd" && "$MODE" != "prompt" && "$MODE" != "autoresearch" ]]; then
  echo "Error: Invalid --mode '$MODE'. Expected prd, prompt, or autoresearch." >&2
  exit 1
fi

if [[ "$DIRECTION" != "minimize" && "$DIRECTION" != "maximize" ]]; then
  echo "Error: Invalid --direction '$DIRECTION'. Expected minimize or maximize." >&2
  exit 1
fi

if ! [[ "$MAX_ITERATIONS" =~ ^[0-9]+$ ]] || [[ "$MAX_ITERATIONS" -lt 1 ]]; then
  echo "Error: --max-iterations must be a positive integer." >&2
  exit 1
fi

WORK_DIR="$(cd "$WORK_DIR" && pwd)"
cd "$WORK_DIR"

# Resolve relative paths against WORK_DIR
if [[ "$PRD_FILE" != /* ]]; then
  PRD_FILE="$WORK_DIR/$PRD_FILE"
fi
if [[ "$PROGRESS_FILE" != /* ]]; then
  PROGRESS_FILE="$WORK_DIR/$PROGRESS_FILE"
fi
if [[ "$RESULTS_FILE" != /* ]]; then
  RESULTS_FILE="$WORK_DIR/$RESULTS_FILE"
fi
if [[ -n "$PROMPT_FILE" && "$PROMPT_FILE" != /* ]]; then
  PROMPT_FILE="$WORK_DIR/$PROMPT_FILE"
fi

# Verify agy binary availability unless dry-run
if [[ "$DRY_RUN" -eq 0 ]] && ! command -v "$AGY_BIN" >/dev/null 2>&1; then
  echo "Error: '$AGY_BIN' binary not found in PATH." >&2
  exit 1
fi

hash_text() {
  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from loop_core import hash_error_output
print(hash_error_output(sys.stdin.read()))
' "$SCRIPT_DIR"
}

has_promise() {
  local expected="$1"
  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from loop_core import promise_matches
content = sys.stdin.read()
sys.exit(0 if promise_matches(content, sys.argv[2]) else 1)
' "$SCRIPT_DIR" "$expected"
}

run_agy_turn() {
  local prompt_path="$1"
  local use_model="$2"
  local use_effort="$3"
  local out_file="$4"

  local cmd=("$AGY_BIN" --dangerously-skip-permissions)
  if [[ -n "$use_model" && "$use_model" != "default" && "$use_model" != "auto" ]]; then
    cmd+=(--model "$use_model")
  fi
  if [[ -n "$use_effort" ]]; then
    cmd+=(--effort "$use_effort")
  fi
  if [[ -n "$PRINT_TIMEOUT" && "$PRINT_TIMEOUT" != "0s" && "$PRINT_TIMEOUT" != "0" ]]; then
    cmd+=(--print-timeout "$PRINT_TIMEOUT")
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] Command: ${cmd[*]} -p=\"\$(cat $prompt_path)\""
    echo "[DRY-RUN] --- Assembled Prompt Preview ---"
    cat "$prompt_path"
    echo "[DRY-RUN] --- End Preview ---"
    return 0
  fi

  local prompt_body
  prompt_body="$(cat "$prompt_path")"
  local rc=0
  "${cmd[@]}" -p="$prompt_body" > "$out_file" 2>&1 || rc=$?
  cat "$out_file"
  return "$rc"
}

run_verify_gate() {
  local cmd="$1"
  local log_file="$2"
  local rc=0
  bash -c "$cmd" > "$log_file" 2>&1 || rc=$?
  return "$rc"
}

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/floating-loop.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# MODE 1: PRD Fresh-Context Loop
# ---------------------------------------------------------------------------
run_prd_mode() {
  if [[ ! -f "$PRD_FILE" ]]; then
    echo "Error: PRD file not found at $PRD_FILE" >&2
    exit 1
  fi

  python3 "$LOOP_CTL" validate-prd "$PRD_FILE"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    python3 "$LOOP_CTL" archive-check --cwd "$WORK_DIR" --prd "$PRD_FILE" --progress "$PROGRESS_FILE"

    if [[ ! -f "$PROGRESS_FILE" ]]; then
      {
        echo "# Floating Loop Progress Log"
        echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo ""
        echo "## Codebase Patterns"
        echo ""
        echo "---"
      } > "$PROGRESS_FILE"
    fi
  fi

  local no_prog_streak=0
  local same_err_streak=0
  local last_err_hash=""
  local last_story_id=""
  local story_retry=0
  local retry_backpressure=""
  local cli_fail_streak=0

  echo "Starting Floating Loop (mode=prd, max_iterations=$MAX_ITERATIONS, cb_threshold=$CB_THRESHOLD)"

  local i=1
  while [[ "$i" -le "$MAX_ITERATIONS" ]]; do
    local next_json="$TMP_DIR/next.json"
    python3 "$LOOP_CTL" next-story "$PRD_FILE" --json > "$next_json"

    local parsed
    parsed="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
st = d.get("story") or {}
fields = [
    "true" if d.get("allPassed") else "false",
    str(d.get("passedCount", 0)),
    str(d.get("total", 0)),
    str(d.get("branchName") or ""),
    str(d.get("verifyCommand") or ""),
    str(d.get("defaultModel") or ""),
    str(d.get("defaultEffort") or ""),
    str(st.get("id") or ""),
    str(st.get("title") or ""),
    str(st.get("hat") or "implementer"),
    str(st.get("model") or ""),
    str(st.get("effort") or ""),
    str(st.get("verifyCommand") or ""),
    str(d.get("blockedCount", 0)),
]
print("\x1f".join(fields))
' "$next_json")"

    local all_passed passed_count total_count branch_name prd_verify prd_model prd_effort
    local story_id story_title story_hat story_model story_effort story_verify blocked_count
    IFS=$'\x1f' read -r all_passed passed_count total_count branch_name prd_verify prd_model prd_effort \
      story_id story_title story_hat story_model story_effort story_verify blocked_count <<< "$parsed"

    if [[ "$all_passed" == "true" ]]; then
      echo ""
      echo "Floating Loop complete: all $total_count/$total_count stories pass!"
      exit 0
    fi

    if [[ -z "$story_id" ]]; then
      echo "Error: Remaining stories ($blocked_count) are blocked on unmet dependencies." >&2
      exit 1
    fi

    # Track retry count per story for automatic model/effort cascade
    if [[ "$story_id" != "$last_story_id" ]]; then
      last_story_id="$story_id"
      story_retry=0
      retry_backpressure=""
    fi

    local eff_model="${story_model:-${prd_model:-$MODEL}}"
    if [[ "$MODEL_EXPLICIT" -eq 1 && -z "$story_model" ]]; then
      eff_model="$MODEL"
    fi
    local eff_effort="${story_effort:-${EFFORT:-$prd_effort}}"
    local eff_verify="${VERIFY_CMD:-${story_verify:-$prd_verify}}"

    if [[ "$story_retry" -gt 0 && "$NO_CASCADE" -eq 0 ]]; then
      if [[ -n "$ESCALATE_MODEL" && "$ESCALATE_MODEL" != "default" ]]; then
        eff_model="$ESCALATE_MODEL"
      fi
      if [[ -n "$ESCALATE_EFFORT" ]]; then
        eff_effort="$ESCALATE_EFFORT"
      elif [[ -n "$eff_effort" ]]; then
        eff_effort="$(python3 "$LOOP_CTL" escalate-effort --current "$eff_effort")"
      fi
    fi

    echo ""
    echo "=================================================================="
    echo "  Floating Loop Iteration $i/$MAX_ITERATIONS — $story_id: $story_title"
    echo "  Progress: $passed_count/$total_count | Hat: $story_hat | Retry: $story_retry | Model: ${eff_model:-default} | Effort: ${eff_effort:-default}"
    echo "=================================================================="

    local prompt_tmp="$TMP_DIR/prompt-$i.md"
    {
      cat "$PROMPTS_DIR/prd-iteration.md"
      echo ""
      if [[ -f "$PROMPTS_DIR/hats/${story_hat}.md" ]]; then
        cat "$PROMPTS_DIR/hats/${story_hat}.md"
        echo ""
      fi
      echo "## Target Story (Iteration $i)"
      echo ""
      echo '```json'
      python3 -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["story"], indent=2))' "$next_json"
      echo '```'
      if [[ -n "$eff_verify" ]]; then
        echo ""
        echo "## Independent Verification Command"
        echo "Run and satisfy before marking passes=true: \`$eff_verify\`"
      fi
      if [[ "$i" -eq 1 ]] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        local dirty_takeover
        dirty_takeover="$(git status --short -- . ':(exclude).floating-loop' ':(exclude)progress.txt' ':(exclude)results.tsv' || true)"
        if [[ -n "$dirty_takeover" ]]; then
          echo ""
          echo "## Interrupted Work Detected on Disk (Mid-Run Takeover)"
          echo "The following uncommitted changes already exist from an interrupted prior iteration."
          echo "Inspect \`git diff\` first: finish and verify them if they belong to $story_id, or revert/fix broken fragments before committing:"
          echo '```'
          printf '%s\n' "$dirty_takeover"
          echo '```'
        fi
      fi
      if [[ -n "$retry_backpressure" ]]; then
        echo ""
        echo "## Previous Iteration Verification Failure (Backpressure)"
        echo "Your previous attempt failed independent verification. Fix the root cause:"
        echo '```'
        printf '%s\n' "$retry_backpressure"
        echo '```'
      fi
    } > "$prompt_tmp"

    if [[ "$DRY_RUN" -eq 1 ]]; then
      run_agy_turn "$prompt_tmp" "$eff_model" "$eff_effort" "$TMP_DIR/out-$i.txt"
      exit 0
    fi

    local fp_before
    fp_before="$(python3 "$LOOP_CTL" fingerprint --cwd "$WORK_DIR")"
    local prd_snapshot="$TMP_DIR/prd-before-$i.json"
    cp "$PRD_FILE" "$prd_snapshot"

    local out_file="$TMP_DIR/out-$i.txt"
    local agy_rc=0
    run_agy_turn "$prompt_tmp" "$eff_model" "$eff_effort" "$out_file" || agy_rc=$?

    if [[ "$agy_rc" -ne 0 ]]; then
      cli_fail_streak=$((cli_fail_streak + 1))
      echo "[floating-loop] Warning: $AGY_BIN exited with status $agy_rc (streak=$cli_fail_streak)." >&2
      if [[ "$cli_fail_streak" -ge 3 ]]; then
        echo "[floating-loop] Circuit breaker OPEN: $AGY_BIN failed $cli_fail_streak consecutive times." >&2
        exit 2
      fi
      local backoff=$((SLEEP_SEC * cli_fail_streak))
      [[ "$backoff" -gt 0 ]] && sleep "$backoff"
      i=$((i + 1))
      continue
    fi
    cli_fail_streak=0

    # Integrity guard: ensure prd.json is still valid JSON, no stories were deleted/added,
    # and no unrelated failing story was secretly flipped to passes=true!
    local prd_integrity_err=""
    prd_integrity_err="$(python3 - "$prd_snapshot" "$PRD_FILE" "$story_id" << 'PY'
import json, sys
before_path, after_path, target_id = sys.argv[1:4]
before = json.load(open(before_path, encoding="utf-8"))
try:
    after = json.load(open(after_path, encoding="utf-8"))
except Exception as exc:
    print(f"prd.json became invalid JSON ({exc})")
    sys.exit(0)
if not isinstance(after, dict) or not isinstance(after.get("userStories"), list):
    print("prd.json lost its userStories array")
    sys.exit(0)
b_stories = {s["id"]: s for s in before["userStories"]}
a_stories = {s.get("id"): s for s in after["userStories"] if isinstance(s, dict)}
if set(b_stories.keys()) != set(a_stories.keys()):
    missing = sorted(set(b_stories) - set(a_stories))
    added = sorted(set(a_stories) - set(b_stories))
    print(f"prd.json story IDs were tampered with (missing={missing}, added={added})")
    sys.exit(0)
illegal = [
    sid for sid, st in a_stories.items()
    if sid != target_id and not b_stories[sid].get("passes") and st.get("passes") is True
]
if illegal:
    print(f"Non-target story/stories illegally marked passes=true in single-story turn: {illegal}")
PY
)"
    if [[ -n "$prd_integrity_err" ]]; then
      echo "[floating-loop] PRD INTEGRITY VIOLATION: $prd_integrity_err — restoring prd.json snapshot." >&2
      cp "$prd_snapshot" "$PRD_FILE"
      story_retry=$((story_retry + 1))
      retry_backpressure="PRD INTEGRITY VIOLATION: $prd_integrity_err. Your prd.json edit was reverted. Only update passes=true on target story $story_id after verification passes."
      local err_hash
      err_hash="$(printf '%s' "$retry_backpressure" | hash_text)"
      if [[ "$err_hash" == "$last_err_hash" ]]; then
        same_err_streak=$((same_err_streak + 1))
      else
        same_err_streak=1
        last_err_hash="$err_hash"
      fi
      if [[ "$CB_THRESHOLD" -gt 0 && "$same_err_streak" -ge "$CB_THRESHOLD" ]]; then
        echo "[floating-loop] Circuit breaker OPEN: repeated PRD integrity violation ($same_err_streak times)." >&2
        exit 2
      fi
      [[ "$SLEEP_SEC" -gt 0 ]] && sleep "$SLEEP_SEC"
      i=$((i + 1))
      continue
    fi

    # Re-read story passes state from prd.json
    local story_now_passes
    story_now_passes="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for s in d.get("userStories", []):
    if s.get("id") == sys.argv[2]:
        print("true" if s.get("passes") else "false")
        sys.exit(0)
print("false")
' "$PRD_FILE" "$story_id")"

    local promise_hit=0
    if has_promise "$COMPLETION_PROMISE" < "$out_file"; then
      promise_hit=1
    fi

    local verify_failed=0
    if [[ -n "$eff_verify" && ( "$story_now_passes" == "true" || "$promise_hit" -eq 1 ) ]]; then
      echo "[floating-loop] Running independent Dual-Gate verification: $eff_verify"
      local vlog="$TMP_DIR/verify-$i.log"
      local vrc=0
      run_verify_gate "$eff_verify" "$vlog" || vrc=$?
      if [[ "$vrc" -eq 0 ]]; then
        echo "[floating-loop] Dual-Gate verification PASSED."
        retry_backpressure=""
        same_err_streak=0
        last_err_hash=""
      else
        verify_failed=1
        local err_tail
        err_tail="$(tail -c 3500 "$vlog" || true)"
        if [[ -z "$err_tail" ]]; then
          err_tail="(command exited with status $vrc and produced no stdout/stderr)"
        fi
        echo "[floating-loop] Dual-Gate verification FAILED for $story_id (exit $vrc) — reverting passes=false." >&2
        python3 "$LOOP_CTL" mark-story "$PRD_FILE" --id "$story_id" --passes false --note "Independent verifyCommand failed (exit $vrc) on iteration $i" >/dev/null
        story_now_passes="false"
        promise_hit=0
        story_retry=$((story_retry + 1))
        retry_backpressure="Command \`$eff_verify\` exited with status $vrc:
$err_tail"

        local err_hash
        err_hash="$(printf '%s' "$retry_backpressure" | hash_text)"
        if [[ "$err_hash" == "$last_err_hash" ]]; then
          same_err_streak=$((same_err_streak + 1))
        else
          same_err_streak=1
          last_err_hash="$err_hash"
        fi
      fi
    elif [[ "$story_now_passes" != "true" ]]; then
      story_retry=$((story_retry + 1))
    fi

    # Check if all stories now pass cleanly
    local after_all
    after_all="$(python3 -c 'import json, sys; d=json.load(open(sys.argv[1])); print("true" if d.get("userStories") and all(s.get("passes") for s in d["userStories"]) else "false")' "$PRD_FILE")"
    if [[ "$after_all" == "true" && "$verify_failed" -eq 0 ]]; then
      echo ""
      echo "Floating Loop completed ALL stories at iteration $i/$MAX_ITERATIONS!"
      exit 0
    fi

    # Workspace progress fingerprinting & Circuit Breaker
    local fp_after
    fp_after="$(python3 "$LOOP_CTL" fingerprint --cwd "$WORK_DIR")"
    if [[ "$fp_after" == "$fp_before" && "$story_now_passes" != "true" ]]; then
      no_prog_streak=$((no_prog_streak + 1))
    else
      no_prog_streak=0
    fi

    if [[ "$CB_THRESHOLD" -gt 0 ]]; then
      if [[ "$no_prog_streak" -ge "$CB_THRESHOLD" ]]; then
        echo "[floating-loop] Circuit breaker OPEN: no workspace progress for $no_prog_streak consecutive iteration(s)." >&2
        exit 2
      fi
      if [[ "$same_err_streak" -ge "$CB_THRESHOLD" ]]; then
        echo "[floating-loop] Circuit breaker OPEN: identical verification error repeated $same_err_streak consecutive time(s)." >&2
        exit 2
      fi
    fi

    [[ "$SLEEP_SEC" -gt 0 ]] && sleep "$SLEEP_SEC"
    i=$((i + 1))
  done

  echo ""
  echo "Floating Loop reached max iterations ($MAX_ITERATIONS) without completing all stories." >&2
  exit 1
}

# ---------------------------------------------------------------------------
# MODE 2: Raw Prompt Fresh-Context Loop
# ---------------------------------------------------------------------------
run_prompt_mode() {
  if [[ -z "$PROMPT_FILE" || ! -f "$PROMPT_FILE" ]]; then
    echo "Error: --mode prompt requires an existing --prompt-file <path>." >&2
    exit 1
  fi

  local no_prog_streak=0
  local same_err_streak=0
  local last_err_hash=""
  local backpressure=""
  local retry_count=0
  local cli_fail_streak=0

  echo "Starting Floating Loop (mode=prompt, max_iterations=$MAX_ITERATIONS, promise=$COMPLETION_PROMISE)"

  local i=1
  while [[ "$i" -le "$MAX_ITERATIONS" ]]; do
    local eff_model="$MODEL"
    local eff_effort="$EFFORT"
    if [[ "$retry_count" -gt 0 && "$NO_CASCADE" -eq 0 ]]; then
      [[ -n "$ESCALATE_MODEL" && "$ESCALATE_MODEL" != "default" ]] && eff_model="$ESCALATE_MODEL"
      if [[ -n "$ESCALATE_EFFORT" ]]; then
        eff_effort="$ESCALATE_EFFORT"
      elif [[ -n "$eff_effort" ]]; then
        eff_effort="$(python3 "$LOOP_CTL" escalate-effort --current "$eff_effort")"
      fi
    fi

    local prompt_tmp="$TMP_DIR/raw-prompt-$i.md"
    {
      cat "$PROMPT_FILE"
      echo ""
      echo "---"
      echo "Iteration $i of $MAX_ITERATIONS. Output <promise>$COMPLETION_PROMISE</promise> ONLY when genuinely complete."
      if [[ -n "$VERIFY_CMD" ]]; then
        echo "Independent verification command: \`$VERIFY_CMD\`"
      fi
      if [[ -n "$backpressure" ]]; then
        echo ""
        echo "## Previous Dual-Gate Verification Failure"
        echo '```'
        printf '%s\n' "$backpressure"
        echo '```'
      fi
    } > "$prompt_tmp"

    if [[ "$DRY_RUN" -eq 1 ]]; then
      run_agy_turn "$prompt_tmp" "$eff_model" "$eff_effort" "$TMP_DIR/out-$i.txt"
      exit 0
    fi

    local fp_before
    fp_before="$(python3 "$LOOP_CTL" fingerprint --cwd "$WORK_DIR")"

    local out_file="$TMP_DIR/out-$i.txt"
    local agy_rc=0
    run_agy_turn "$prompt_tmp" "$eff_model" "$eff_effort" "$out_file" || agy_rc=$?
    if [[ "$agy_rc" -ne 0 ]]; then
      cli_fail_streak=$((cli_fail_streak + 1))
      echo "[floating-loop] Warning: $AGY_BIN exited with status $agy_rc (streak=$cli_fail_streak)." >&2
      if [[ "$cli_fail_streak" -ge 3 ]]; then
        echo "[floating-loop] Circuit breaker OPEN: $AGY_BIN failed $cli_fail_streak consecutive times." >&2
        exit 2
      fi
      local backoff=$((SLEEP_SEC * cli_fail_streak))
      [[ "$backoff" -gt 0 ]] && sleep "$backoff"
      i=$((i + 1))
      continue
    fi
    cli_fail_streak=0

    if has_promise "$COMPLETION_PROMISE" < "$out_file"; then
      if [[ -n "$VERIFY_CMD" ]]; then
        local vlog="$TMP_DIR/verify-$i.log"
        local vrc=0
        run_verify_gate "$VERIFY_CMD" "$vlog" || vrc=$?
        if [[ "$vrc" -eq 0 ]]; then
          echo "Floating Loop completed at iteration $i: promise + verify-cmd PASSED."
          exit 0
        else
          local err_tail
          err_tail="$(tail -c 3500 "$vlog" || true)"
          if [[ -z "$err_tail" ]]; then
            err_tail="(command exited with status $vrc and produced no stdout/stderr)"
          fi
          backpressure="Command \`$VERIFY_CMD\` exited with status $vrc:
$err_tail"
          retry_count=$((retry_count + 1))
          local err_hash
          err_hash="$(printf '%s' "$backpressure" | hash_text)"
          if [[ "$err_hash" == "$last_err_hash" ]]; then
            same_err_streak=$((same_err_streak + 1))
          else
            same_err_streak=1
            last_err_hash="$err_hash"
          fi
        fi
      else
        echo "Floating Loop completed at iteration $i: detected <promise>$COMPLETION_PROMISE</promise>."
        exit 0
      fi
    fi

    local fp_after
    fp_after="$(python3 "$LOOP_CTL" fingerprint --cwd "$WORK_DIR")"
    if [[ "$fp_after" == "$fp_before" ]]; then
      no_prog_streak=$((no_prog_streak + 1))
    else
      no_prog_streak=0
    fi

    if [[ "$CB_THRESHOLD" -gt 0 ]]; then
      if [[ "$no_prog_streak" -ge "$CB_THRESHOLD" ]]; then
        echo "[floating-loop] Circuit breaker OPEN: no progress for $no_prog_streak consecutive iteration(s)." >&2
        exit 2
      fi
      if [[ "$same_err_streak" -ge "$CB_THRESHOLD" ]]; then
        echo "[floating-loop] Circuit breaker OPEN: identical verify error repeated $same_err_streak times." >&2
        exit 2
      fi
    fi

    [[ "$SLEEP_SEC" -gt 0 ]] && sleep "$SLEEP_SEC"
    i=$((i + 1))
  done

  echo "Floating Loop reached max iterations ($MAX_ITERATIONS) without verified completion." >&2
  exit 1
}

# ---------------------------------------------------------------------------
# MODE 3: Autoresearch Scientific Keep-or-Revert Loop
# ---------------------------------------------------------------------------
run_autoresearch_mode() {
  if [[ -z "$METRIC_CMD" ]]; then
    echo "Error: --mode autoresearch requires --metric-cmd <cmd>." >&2
    exit 1
  fi

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Error: --mode autoresearch must run inside a git repository." >&2
    exit 1
  fi

  if [[ "$DRY_RUN" -eq 0 ]]; then
    local initial_dirty
    initial_dirty="$(git status --porcelain -- . ':(exclude)results.tsv' ':(exclude).floating-loop')"
    if [[ -n "$initial_dirty" ]]; then
      echo "Error: --mode autoresearch requires a clean git working tree so reverts cannot erase uncommitted work." >&2
      echo "Commit or stash these files first:" >&2
      printf '%s\n' "$initial_dirty" >&2
      exit 1
    fi
  fi

  local baseline_out="$TMP_DIR/baseline-metric.txt"
  if ! bash -c "$METRIC_CMD" > "$baseline_out" 2>&1; then
    echo "Error: Baseline --metric-cmd failed:" >&2
    cat "$baseline_out" >&2
    exit 1
  fi

  local best_metric
  best_metric="$(python3 "$LOOP_CTL" eval-metric --direction "$DIRECTION" --json < "$baseline_out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["ok"]; print(d["value"])')"
  local base_sha
  base_sha="$(git rev-parse --short HEAD)"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ ! -f "$RESULTS_FILE" ]]; then
      printf "iteration\ttimestamp\tcommit\tmetric\tbest_metric\tdecision\tnote\n" > "$RESULTS_FILE"
    fi
    printf "0\t%s\t%s\t%s\t%s\tbaseline\tinitial measurement\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$base_sha" "$best_metric" "$best_metric" >> "$RESULTS_FILE"
  fi

  echo "Starting Floating Loop (mode=autoresearch, direction=$DIRECTION, baseline=$best_metric, max_iterations=$MAX_ITERATIONS)"

  local no_improve_streak=0
  local cli_fail_streak=0
  local last_note=""
  local i=1
  while [[ "$i" -le "$MAX_ITERATIONS" ]]; do
    local before_sha
    before_sha="$(git rev-parse HEAD)"

    local prompt_tmp="$TMP_DIR/research-$i.md"
    {
      cat "$PROMPTS_DIR/autoresearch.md"
      echo ""
      echo "## Experiment Target (Iteration $i/$MAX_ITERATIONS)"
      echo "- Direction: **$DIRECTION**"
      echo "- Metric command: \`$METRIC_CMD\`"
      echo "- Current best metric: **$best_metric**"
      if [[ -n "$VERIFY_CMD" ]]; then
        echo "- Verification command: \`$VERIFY_CMD\`"
      fi
      if [[ -n "$GOAL_TEXT" ]]; then
        echo "- Goal: $GOAL_TEXT"
      fi
      if [[ -n "$PROMPT_FILE" && -f "$PROMPT_FILE" ]]; then
        echo ""
        echo "## Additional Instructions"
        cat "$PROMPT_FILE"
      fi
      if [[ -n "$last_note" ]]; then
        echo ""
        echo "## Previous Attempt Feedback"
        printf '%s\n' "$last_note"
      fi
      echo ""
      echo "## Recent results.tsv"
      echo '```tsv'
      if [[ -f "$RESULTS_FILE" ]]; then
        tail -n 15 "$RESULTS_FILE"
      else
        echo "(dry-run: baseline=$best_metric)"
      fi
      echo '```'
    } > "$prompt_tmp"

    if [[ "$DRY_RUN" -eq 1 ]]; then
      run_agy_turn "$prompt_tmp" "$MODEL" "$EFFORT" "$TMP_DIR/out-$i.txt"
      exit 0
    fi

    echo ""
    echo "=================================================================="
    echo "  Autoresearch Iteration $i/$MAX_ITERATIONS | Best ($DIRECTION): $best_metric"
    echo "=================================================================="

    local out_file="$TMP_DIR/out-$i.txt"
    local agy_rc=0
    run_agy_turn "$prompt_tmp" "$MODEL" "$EFFORT" "$out_file" || agy_rc=$?
    if [[ "$agy_rc" -ne 0 ]]; then
      cli_fail_streak=$((cli_fail_streak + 1))
      echo "[autoresearch] Warning: $AGY_BIN exited with status $agy_rc (streak=$cli_fail_streak)." >&2
      if [[ "$cli_fail_streak" -ge 3 ]]; then
        echo "[autoresearch] Circuit breaker OPEN: $AGY_BIN failed $cli_fail_streak consecutive times." >&2
        exit 2
      fi
      local backoff=$((SLEEP_SEC * cli_fail_streak))
      [[ "$backoff" -gt 0 ]] && sleep "$backoff"
      i=$((i + 1))
      continue
    fi
    cli_fail_streak=0

    local after_sha
    after_sha="$(git rev-parse HEAD)"
    # If the agent left uncommitted edits without committing, commit them so keep/revert is atomic
    local dirty_changes
    dirty_changes="$(git status --porcelain -- . ':(exclude)results.tsv' ':(exclude).floating-loop')"
    if [[ -n "$dirty_changes" ]]; then
      git add -A -- . ':(exclude)results.tsv' ':(exclude).floating-loop'
      git commit -m "exp: autoresearch iteration $i" >/dev/null 2>&1 || true
      after_sha="$(git rev-parse HEAD)"
    fi

    if [[ "$after_sha" == "$before_sha" ]]; then
      no_improve_streak=$((no_improve_streak + 1))
      printf "%d\t%s\t%s\tNA\t%s\tno_change\tno code changes produced\n" \
        "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --short HEAD)" "$best_metric" >> "$RESULTS_FILE"
      last_note="Previous iteration produced no code changes. Form a concrete hypothesis and modify code."
    else
      local cand_short
      cand_short="$(git rev-parse --short "$after_sha")"
      local cand_subject
      cand_subject="$(git log -1 --pretty=%s "$after_sha" | tr '\t\n' '  ')"

      # 1. Verify correctness gate if configured
      local verify_ok=1
      if [[ -n "$VERIFY_CMD" ]]; then
        local vlog="$TMP_DIR/research-verify-$i.log"
        if ! run_verify_gate "$VERIFY_CMD" "$vlog"; then
          verify_ok=0
          local vtail
          vtail="$(tail -c 2000 "$vlog" || true)"
          echo "[autoresearch] Verify FAILED — reverting $cand_short -> $(git rev-parse --short "$before_sha")"
          cp "$RESULTS_FILE" "$TMP_DIR/results.tsv.bak"
          git reset --hard "$before_sha" >/dev/null
          cp "$TMP_DIR/results.tsv.bak" "$RESULTS_FILE"
          no_improve_streak=$((no_improve_streak + 1))
          printf "%d\t%s\t%s\tNA\t%s\trevert_verify\t%s\n" \
            "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$cand_short" "$best_metric" "$cand_subject" >> "$RESULTS_FILE"
          last_note="Candidate $cand_short ($cand_subject) failed verification and was reverted:\n$vtail"
        fi
      fi

      # 2. Measure metric & decide Keep vs Revert
      if [[ "$verify_ok" -eq 1 ]]; then
        local mlog="$TMP_DIR/research-metric-$i.log"
        if bash -c "$METRIC_CMD" > "$mlog" 2>&1; then
          local eval_json
          eval_json="$(python3 "$LOOP_CTL" eval-metric --best "$best_metric" --direction "$DIRECTION" --json < "$mlog" || true)"
          local ok_flag new_val improved_flag
          IFS=$'\x1f' read -r ok_flag new_val improved_flag <<< "$(python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    print("false\x1fNA\x1ffalse")
    sys.exit(0)
print(
    ("true" if d.get("ok") else "false") + "\x1f" +
    str(d.get("value", "NA")) + "\x1f" +
    ("true" if d.get("improved") else "false")
)
' "$eval_json")"

          if [[ "$ok_flag" == "true" && "$improved_flag" == "true" ]]; then
            echo "[autoresearch] KEEP $cand_short: $best_metric -> $new_val ($DIRECTION)"
            best_metric="$new_val"
            no_improve_streak=0
            printf "%d\t%s\t%s\t%s\t%s\tkeep\t%s\n" \
              "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$cand_short" "$new_val" "$best_metric" "$cand_subject" >> "$RESULTS_FILE"
            last_note="Kept $cand_short ($cand_subject): improved metric to $best_metric."
          else
            echo "[autoresearch] REVERT $cand_short: got $new_val (best remains $best_metric)"
            cp "$RESULTS_FILE" "$TMP_DIR/results.tsv.bak"
            git reset --hard "$before_sha" >/dev/null
            cp "$TMP_DIR/results.tsv.bak" "$RESULTS_FILE"
            no_improve_streak=$((no_improve_streak + 1))
            printf "%d\t%s\t%s\t%s\t%s\trevert\t%s\n" \
              "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$cand_short" "$new_val" "$best_metric" "$cand_subject" >> "$RESULTS_FILE"
            last_note="Reverted $cand_short ($cand_subject): metric $new_val did not improve upon $best_metric."
          fi
        else
          echo "[autoresearch] Metric command errored — reverting $cand_short"
          cp "$RESULTS_FILE" "$TMP_DIR/results.tsv.bak"
          git reset --hard "$before_sha" >/dev/null
          cp "$TMP_DIR/results.tsv.bak" "$RESULTS_FILE"
          no_improve_streak=$((no_improve_streak + 1))
          printf "%d\t%s\t%s\tERR\t%s\trevert_metric_err\t%s\n" \
            "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$cand_short" "$best_metric" "$cand_subject" >> "$RESULTS_FILE"
          last_note="Reverted $cand_short ($cand_subject): metric command failed."
        fi
      fi
    fi

    if [[ "$CB_THRESHOLD" -gt 0 && "$no_improve_streak" -ge "$CB_THRESHOLD" ]]; then
      echo "[autoresearch] Circuit breaker OPEN: no improvement for $no_improve_streak consecutive iteration(s). Best=$best_metric."
      exit 0
    fi

    [[ "$SLEEP_SEC" -gt 0 ]] && sleep "$SLEEP_SEC"
    i=$((i + 1))
  done

  echo ""
  echo "Autoresearch finished $MAX_ITERATIONS iteration(s). Best metric ($DIRECTION): $best_metric"
  echo "Full experiment log: $RESULTS_FILE"
  exit 0
}

case "$MODE" in
  prd) run_prd_mode ;;
  prompt) run_prompt_mode ;;
  autoresearch) run_autoresearch_mode ;;
esac
