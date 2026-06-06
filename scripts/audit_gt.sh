#!/usr/bin/env bash
# audit_gt.sh : independent, paranoid re-check of Stage 1 after PASS_TO_PASS.
# Verifies everything that verify_gt.sh does NOT guarantee, in particular that
# every PASS_TO_PASS name is a REAL test that actually runs and passes (a typo
# would be silently skipped by verify_gt's single-regex run).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
GOMOD="$ROOT/.cache/gomod"; mkdir -p "$GOMOD"
IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"
IDS="1284 1314 1423 1444 1476"
FAILS=0

sandbox(){ docker run --rm -v "$REPO":/workspace -v "$GOMOD":/go/pkg/mod -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$1"; git -C "$REPO" reset --hard --quiet "$1"; git -C "$REPO" clean -fdq; }
apply(){ git -C "$REPO" apply --recount --ignore-whitespace "$1" 2>/dev/null || patch -d "$REPO" -p1 --fuzz=3 < "$1"; }

for ID in $IDS; do
  DIR="$ROOT/eval/tasks/validator-$ID"; J="$DIR/instance.json"
  echo "===== validator-$ID ====="

  # ---- (A) static invariants on instance.json (no Docker) ----
  python3 - "$J" "$DIR" <<'PY' || { echo "  STATIC: FAIL"; }
import json, re, sys, os
J, D = sys.argv[1], sys.argv[2]
d = json.load(open(J))
req = ["instance_id","repo","base_commit","problem_statement","patch",
       "test_patch","FAIL_TO_PASS","PASS_TO_PASS","go_version"]
ftp, ptp = d.get("FAIL_TO_PASS",[]), d.get("PASS_TO_PASS",[])
checks = {
 "has all required fields": all(k in d for k in req),
 "base_commit is 40-hex": bool(re.fullmatch(r"[0-9a-f]{40}", d.get("base_commit",""))),
 "FAIL_TO_PASS non-empty": len(ftp) > 0,
 "PASS_TO_PASS non-empty": len(ptp) > 0,
 "PASS_TO_PASS sorted+deduped": ptp == sorted(set(ptp)),
 "no FAIL_TO_PASS in PASS_TO_PASS": not (set(ftp) & set(ptp)),
 "all PTP names valid Test fns": all(re.fullmatch(r"Test[A-Za-z0-9_]+", t) for t in ptp),
 "embedded patch == fix.patch file": d.get("patch","") == open(os.path.join(D,"fix.patch")).read(),
}
bad = [k for k,v in checks.items() if not v]
for k,v in checks.items():
    print(f"  [{'ok' if v else 'XX'}] {k}")
print(f"  PASS_TO_PASS count = {len(ptp)} ; FAIL_TO_PASS = {ftp}")
sys.exit(1 if bad else 0)
PY
  [ $? -ne 0 ] && FAILS=$((FAILS+1))

  # ---- (B) deep liveness: every PASS_TO_PASS test really runs & passes at base+fix ----
  BASE="$(python3 -c "import json;print(json.load(open('$J'))['base_commit'])")"
  NPTP="$(python3 -c "import json;print(len(json.load(open('$J'))['PASS_TO_PASS']))")"
  RE="$(python3 -c "import json;print('^('+'|'.join(json.load(open('$J'))['PASS_TO_PASS'])+')\$')")"
  git -C "$REPO" cat-file -e "$BASE" 2>/dev/null || git -C "$REPO" fetch --all --tags --quiet
  reset_base "$BASE"
  # install gold test (repro preferred) + the fix, exactly like the gate's pass state
  if [ -f "$DIR/repro_test.go" ]; then cp "$DIR/repro_test.go" "$REPO/zz_v${ID}_repro_test.go"
  elif [ -s "$DIR/test.patch" ]; then apply "$DIR/test.patch"; fi
  apply "$DIR/fix.patch"
  OUT="$(sandbox "go test -v -run '$RE' . 2>/dev/null")"
  reset_base "$BASE"
  NPASS="$(printf '%s\n' "$OUT" | grep -c '^--- PASS: ')"
  NFAIL="$(printf '%s\n' "$OUT" | grep -c '^--- FAIL: ')"
  if [ "$NPASS" -eq "$NPTP" ] && [ "$NFAIL" -eq 0 ]; then
    echo "  [ok] deep liveness: $NPASS/$NPTP PASS_TO_PASS tests actually ran & passed (0 failed)"
  else
    echo "  [XX] deep liveness: ran=$NPASS expected=$NPTP failed=$NFAIL  <-- MISMATCH"
    FAILS=$((FAILS+1))
  fi
done

echo
echo "===== git state ====="
cd "$ROOT"
git status --porcelain >/tmp/_st 2>/dev/null
[ -s /tmp/_st ] && echo "  [..] working tree has uncommitted changes (expected before you commit):" && git status --short | sed 's/^/      /'
echo "  tags: $(git tag | tr '\n' ' ')"
echo "  gate-gt -> $(git rev-list -n1 gate-gt 2>/dev/null | cut -c1-12)   HEAD -> $(git rev-parse --short=12 HEAD 2>/dev/null)"

echo
if [ "$FAILS" -eq 0 ]; then
  echo "AUDIT RESULT: ALL CHECKS PASSED  ✅  (Phase A is sound)"
else
  echo "AUDIT RESULT: $FAILS check group(s) FAILED  ❌  (see [XX] above)"
fi
