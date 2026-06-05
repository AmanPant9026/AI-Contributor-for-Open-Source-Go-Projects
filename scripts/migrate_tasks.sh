#!/usr/bin/env bash
# One-time reorganizer: eval/tasks/validator-<id>.<ext>  ->  eval/tasks/validator-<id>/<short>
# Moves (never deletes) each instance's files into its own folder. Idempotent:
# re-running after migration is a no-op.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$ROOT/eval/tasks"
cd "$T"

ids="$(ls validator-*.json 2>/dev/null | sed -E 's/^validator-([0-9]+)\.json$/\1/' || true)"
if [ -z "$ids" ]; then echo "nothing to migrate (already grouped, or no instances)"; exit 0; fi

for id in $ids; do
  d="validator-$id"
  mkdir -p "$d"
  [ -f "validator-$id.json" ]          && mv "validator-$id.json"          "$d/instance.json"
  [ -f "validator-$id.fix.patch" ]     && mv "validator-$id.fix.patch"     "$d/fix.patch"
  [ -f "validator-$id.test.patch" ]    && mv "validator-$id.test.patch"    "$d/test.patch"
  [ -f "validator-$id.repro_test.go" ] && mv "validator-$id.repro_test.go" "$d/repro_test.go"
  [ -f "validator-$id.tests.txt" ]     && mv "validator-$id.tests.txt"     "$d/tests.txt"
  [ -f "_src-$id.json" ]               && mv "_src-$id.json"               "$d/src.json"
  echo "grouped: $d/  ($(ls "$d" | tr '\n' ' '))"
done

# keep scratch out of git under the new layout
gi="$ROOT/.gitignore"
grep -q 'eval/tasks/\*/src.json'   "$gi" 2>/dev/null || printf '\neval/tasks/*/src.json\neval/tasks/*/tests.txt\n' >> "$gi"
echo "done. each bug now lives in eval/tasks/validator-<id>/"
