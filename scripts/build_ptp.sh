#!/usr/bin/env bash
# build_ptp.sh <id> : derive PASS_TO_PASS for one instance (SWE-bench style).
#
# PASS_TO_PASS = tests in the package(s) the fix touches that pass BOTH:
#   (a) at base, with no change at all, and
#   (b) at base + gold test + gold fix,
# minus the FAIL_TO_PASS tests. The intersection guarantees we only record
# already-stable tests, so they make a trustworthy regression guard. Result is
# written into eval/tasks/validator-<id>/instance.json.
#
# Run (a) WITHOUT the gold test installed, so a test_patch that references
# not-yet-existing symbols (e.g. 1284's VarWithKey) can't break compilation of
# the baseline run.
set -uo pipefail
ID="${1:?usage: build_ptp.sh <id e.g. 1284>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
GOMOD="$ROOT/.cache/gomod"; mkdir -p "$GOMOD"
IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"
DIR="$ROOT/eval/tasks/validator-$ID"
J="$DIR/instance.json"; FIX="$DIR/fix.patch"; TST="$DIR/test.patch"; REPRO="$DIR/repro_test.go"

fail(){ echo "FAIL: $1"; exit 1; }
[ -f "$J" ] || fail "$J not found"
[ -f "$FIX" ] || fail "$FIX not found"
BASE="$(python3 -c "import json;print(json.load(open('$J'))['base_commit'])")"

# packages touched by the fix (directories of changed .go files); root file -> "."
PKGS="$(grep -E '^\+\+\+ b/' "$FIX" | sed -E 's#^\+\+\+ b/##' | grep -E '\.go$' \
        | xargs -n1 dirname 2>/dev/null | sort -u \
        | sed -E 's#^\.$#.#; s#^([^.].*)$#./\1#' | tr '\n' ' ')"
[ -n "${PKGS// }" ] || PKGS="."
echo "== build PASS_TO_PASS for validator-$ID  (packages: $PKGS) =="

sandbox(){ docker run --rm -v "$REPO":/workspace -v "$GOMOD":/go/pkg/mod -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$BASE"; git -C "$REPO" reset --hard --quiet "$BASE"; git -C "$REPO" clean -fdq; }
apply(){ git -C "$REPO" apply --recount --ignore-whitespace "$1" 2>/dev/null || patch -d "$REPO" -p1 --fuzz=3 < "$1"; }
# print top-level (non-subtest) passing test names in PKGS
passers(){ sandbox "go test -v $PKGS 2>/dev/null" \
  | grep -E '^--- PASS: ' | sed -E 's#^--- PASS: (Test[A-Za-z0-9_]+).*#\1#' | sort -u; }

git -C "$REPO" cat-file -e "$BASE" 2>/dev/null || git -C "$REPO" fetch --all --tags --quiet

# (a) baseline: base, no gold test, no fix
reset_base || fail "checkout base"
passers > "$DIR/.before.tmp"
echo "passing at base:     $(grep -c . "$DIR/.before.tmp" || echo 0)"

# (b) fixed: base + gold test + fix
reset_base
if [ -f "$REPRO" ]; then cp "$REPRO" "$REPO/zz_v${ID}_repro_test.go"
elif [ -s "$TST" ]; then apply "$TST" || fail "apply test patch"; fi
apply "$FIX" || fail "apply fix"
passers > "$DIR/.after.tmp"
echo "passing at base+fix: $(grep -c . "$DIR/.after.tmp" || echo 0)"
reset_base

# FAIL_TO_PASS names to exclude (from repro file if authored, else JSON)
if [ -f "$REPRO" ]; then
  grep -oE 'func Test[A-Za-z0-9_]+' "$REPRO" | sed 's/func //' | sort -u > "$DIR/.ftp.tmp"
else
  python3 -c "import json;print('\n'.join(json.load(open('$J')).get('FAIL_TO_PASS',[])))" | sort -u > "$DIR/.ftp.tmp"
fi

# PASS_TO_PASS = (before ∩ after) − FAIL_TO_PASS
comm -12 "$DIR/.before.tmp" "$DIR/.after.tmp" | grep -vxF -f "$DIR/.ftp.tmp" > "$DIR/.ptp.tmp" || true
echo "PASS_TO_PASS:        $(grep -c . "$DIR/.ptp.tmp" || echo 0) tests"

python3 - "$J" "$DIR/.ptp.tmp" <<'PY'
import json, sys
J, tmp = sys.argv[1], sys.argv[2]
ptp = sorted({l.strip() for l in open(tmp) if l.strip()})
d = json.load(open(J)); d["PASS_TO_PASS"] = ptp
json.dump(d, open(J, "w"), indent=2, ensure_ascii=False)
print("wrote PASS_TO_PASS (%d) into instance.json" % len(ptp))
PY

rm -f "$DIR/.before.tmp" "$DIR/.after.tmp" "$DIR/.ftp.tmp" "$DIR/.ptp.tmp"
echo "done: validator-$ID"
