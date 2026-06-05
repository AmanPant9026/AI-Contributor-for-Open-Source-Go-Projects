#!/usr/bin/env bash
# gate-gt: verify a ground-truth instance is trustworthy:
#   - at base (no fix), the reproduction test FAILS  (empty patch -> not resolved)
#   - with the gold patch, the reproduction test PASSES (gold -> resolved)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
BASE="v10.24.0"
IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:go1.22}"
TESTSRC="$ROOT/eval/tasks/validator-1314.repro_test.go"
PATCHFILE="$ROOT/eval/tasks/validator-1314.fix.patch"
TESTNAME="TestIssue1314PostcodeIso3166Alpha2Field"

fail(){ echo "FAIL: $1"; exit 1; }
sandbox(){ docker run --rm -v "$REPO":/workspace -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$BASE"; \
  git -C "$REPO" reset --hard --quiet "$BASE"; git -C "$REPO" clean -fdq; }

echo "=== gate-gt: validator-1314 ==="
[ -d "$REPO/.git" ] || fail "validator checkout missing at $REPO (run 'make check-env' once to clone it)"

echo "--- reset checkout to base ($BASE) ---"
reset_base || fail "cannot checkout $BASE"
echo "    HEAD=$(git -C "$REPO" rev-parse --short HEAD)"

echo "--- drop in the reproduction test ---"
cp "$TESTSRC" "$REPO/zz_issue1314_repro_test.go"

echo "--- [1/2] BASE, no fix: repro must FAIL ---"
if sandbox "go test -run $TESTNAME ." ; then
  reset_base; fail "repro PASSED at base — bug not reproduced (base may already contain the fix)"
fi
echo "ok: bug reproduced at base (empty patch -> not resolved)"

echo "--- apply gold fix (PR #1359) ---"
git -C "$REPO" apply --recount --ignore-whitespace "$PATCHFILE" 2>/dev/null \
  || patch -d "$REPO" -p1 --fuzz=3 < "$PATCHFILE" \
  || { reset_base; fail "could not apply gold patch"; }

echo "--- [2/2] gold fix applied: repro must PASS ---"
sandbox "go test -run $TESTNAME ." || { reset_base; fail "repro FAILED with gold fix applied"; }
echo "ok: gold patch resolves the issue (gold -> resolved)"

echo "--- cleanup (restore clean base) ---"
reset_base

echo ""
echo "PASSED: gate-gt for validator-1314  (gold resolves, empty fails)."
echo "Lock it:"
echo "    git add eval scripts/verify_gt.sh && git commit -m 'stage 1: validator-1314 ground truth' && git tag gate-gt-1314"
