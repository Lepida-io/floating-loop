#!/usr/bin/env bash
# Unified test runner for Floating Loop.
# Runs Antigravity strict plugin validation, syntax checks, Python unit/swarm tests,
# and external CLI integration tests.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== 1/4: Antigravity Strict Plugin Validation ==="
agy plugin validate . --strict

echo ""
echo "=== 2/4: Python Syntax & Bytecode Check ==="
python3 -m py_compile scripts/loop_core.py scripts/stop-hook.py scripts/guard-hook.py scripts/loop-ctl.py
python3 - <<'EOF'
import ast
from pathlib import Path

path = Path("scripts/workflows/floating_swarm.py")
compile(
    path.read_text(encoding="utf-8"),
    str(path),
    "exec",
    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
)
print(f"{path} compiled with top-level await")
EOF
echo "OK"

echo ""
echo "=== 3/4: Python Unit, Hook & Swarm Workflow Tests ==="
python3 -m unittest discover -s tests -v

echo ""
echo "=== 4/4: External Fresh-Context CLI Integration Tests ==="
bash tests/test_external_loop.sh

echo ""
echo "ALL FLOATING LOOP CHECKS PASSED!"
