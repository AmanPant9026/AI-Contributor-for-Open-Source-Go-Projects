#!/usr/bin/env bash
# gate-3 -- the Stage 3 tools are correct iff their unit tests pass.
# Pure + (monkeypatched) sandbox tests: no Docker, no LLM required.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== gate-3: Stage 3 tool unit tests ==="
python -m pytest tests/test_tools.py -q || { echo "FAIL: gate-3"; exit 1; }
echo "PASSED: gate-3"
