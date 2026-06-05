#!/usr/bin/env bash
# build_gt.sh <pr_number> [id] — extract a ground-truth instance from a merged PR
# into its own folder:  eval/tasks/validator-<id>/{instance.json,fix.patch,test.patch,tests.txt,src.json}
set -euo pipefail
PR="${1:?usage: build_gt.sh <pr_number> [id]}"
ID="${2:-$PR}"
SLUG="go-playground/validator"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
DIR="$ROOT/eval/tasks/validator-$ID"; mkdir -p "$DIR"

command -v gh >/dev/null || { echo "need gh"; exit 1; }
[ -d "$REPO/.git" ] || { echo "validator clone missing; run 'make check-env' once"; exit 1; }

echo "== PR #$PR -> validator-$ID =="
MC="$(gh pr view "$PR" --repo "$SLUG" --json mergeCommit --jq '.mergeCommit.oid')"
[ -n "$MC" ] || { echo "no merge commit (is #$PR MERGED?)"; exit 1; }
git -C "$REPO" fetch --all --tags --quiet
git -C "$REPO" cat-file -e "$MC" 2>/dev/null || git -C "$REPO" fetch --quiet origin "$MC" 2>/dev/null || true
BASE="$(git -C "$REPO" rev-parse "${MC}^1")"
echo "merge=$MC"; echo "base =$BASE"

git -C "$REPO" diff "${MC}^1" "$MC" -- ':(exclude)*_test.go' > "$DIR/fix.patch"
git -C "$REPO" diff "${MC}^1" "$MC" -- '*_test.go'           > "$DIR/test.patch"
echo "fix.patch=$(wc -l <"$DIR/fix.patch") lines  test.patch=$(wc -l <"$DIR/test.patch") lines"

ADDED="$(grep -E '^\+func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/^\+func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true)"
if [ -n "$ADDED" ]; then
  printf '%s\n' "$ADDED" > "$DIR/tests.txt"
else
  { grep -E '^@@.*func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/.*func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true; } > "$DIR/tests.txt"
fi
echo "FAIL_TO_PASS:"; sed 's/^/  /' "$DIR/tests.txt"

ISSUE="$(gh pr view "$PR" --repo "$SLUG" --json closingIssuesReferences --jq '.closingIssuesReferences[0].number // empty' 2>/dev/null || true)"
if [ -n "${ISSUE:-}" ]; then
  gh issue view "$ISSUE" --repo "$SLUG" --json title,body > "$DIR/src.json"; echo "problem_statement <- issue #$ISSUE"
else
  gh pr view "$PR" --repo "$SLUG" --json title,body > "$DIR/src.json"; echo "problem_statement <- PR #$PR (REVIEW for fix leakage)"
fi

python3 - "$ID" "$PR" "$BASE" "$MC" "$DIR" "${ISSUE:-}" <<'PY'
import json,sys
ID,PR,BASE,MC,DIR,ISSUE=sys.argv[1:7]
src=json.load(open(f"{DIR}/src.json"))
ps=((src.get("title") or "")+"\n\n"+(src.get("body") or "")).strip()
ftp=[l.strip() for l in open(f"{DIR}/tests.txt") if l.strip()]
inst={"instance_id":f"go-playground__validator-{ID}","repo":"go-playground/validator","base_commit":BASE,
 "problem_statement":ps,"patch":open(f"{DIR}/fix.patch").read(),
 "test_patch":open(f"{DIR}/test.patch").read(),"FAIL_TO_PASS":ftp,"PASS_TO_PASS":[],
 "go_version":"1.24","fix_pr":int(PR),"issue":(int(ISSUE) if ISSUE else None),"merge_commit":MC}
json.dump(inst,open(f"{DIR}/instance.json","w"),indent=2,ensure_ascii=False)
print("wrote validator-%s/instance.json | FAIL_TO_PASS=%s"%(ID,ftp))
PY
echo "done: validator-$ID"
