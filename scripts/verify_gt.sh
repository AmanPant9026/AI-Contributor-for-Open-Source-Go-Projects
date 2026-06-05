#!/usr/bin/env bash
# verify_gt.sh <id> : gate-gt for one instance (new folder layout).
# Files live in eval/tasks/validator-<id>/ : instance.json, fix.patch, test.patch, repro_test.go
# An authored repro_test.go (if present) takes precedence and drives the test name(s).
set -uo pipefail
ID="${1:?usage: verify_gt.sh <id e.g. 1284>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"
DIR="$ROOT/eval/tasks/validator-$ID"
J="$DIR/instance.json"
FIX="$DIR/fix.patch"
TST="$DIR/test.patch"
REPRO="$DIR/repro_test.go"

fail(){ echo "FAIL: $1"; exit 1; }
[ -f "$J" ] || fail "$J not found (run scripts/build_gt.sh $ID, then scripts/migrate_tasks.sh)"
BASE="$(python3 -c "import json;print(json.load(open('$J'))['base_commit'])")"

if [ -f "$REPRO" ]; then
  MODE=repro
  RE="^($(grep -oE 'func Test[A-Za-z0-9_]+' "$REPRO" | sed -E 's/func //' | paste -sd '|' -))\$"
else
  MODE=patch
  RE="$(python3 -c "import json;f=json.load(open('$J'))['FAIL_TO_PASS'];print('^('+'|'.join(f)+')\$')")"
fi
[ "$RE" != '^()$' ] || fail "no test names for $ID"

sandbox(){ docker run --rm -v "$REPO":/workspace -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$BASE"; git -C "$REPO" reset --hard --quiet "$BASE"; git -C "$REPO" clean -fdq; }
apply(){ git -C "$REPO" apply --recount --ignore-whitespace "$1" 2>/dev/null || patch -d "$REPO" -p1 --fuzz=3 < "$1"; }
install_tests(){
  if [ "$MODE" = repro ]; then cp "$REPRO" "$REPO/zz_v${ID}_repro_test.go"
  elif [ -s "$TST" ]; then apply "$TST" || fail "cannot apply test patch"
  else fail "no test patch / repro for $ID"; fi
}

echo "=== gate-gt: validator-$ID  (base ${BASE:0:12}, mode=$MODE)"
echo "    tests=$RE"
git -C "$REPO" cat-file -e "$BASE" 2>/dev/null || git -C "$REPO" fetch --all --tags --quiet
reset_base || fail "cannot checkout base $BASE"
install_tests

echo "--- [1/2] base, no code fix: tests must FAIL ---"
if sandbox "go test -run '$RE' ./..." ; then reset_base; fail "tests PASSED at base — not capturing the bug"; fi
echo "ok: fails at base"

apply "$FIX" || { reset_base; fail "cannot apply code patch"; }
echo "--- [2/2] code fix applied: tests must PASS ---"
sandbox "go test -run '$RE' ./..." || { reset_base; fail "tests FAILED with fix applied"; }
echo "ok: passes with fix"

reset_base
echo ""
echo "PASSED: gate-gt for validator-$ID"
