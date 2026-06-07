#!/usr/bin/env bash
# gate-4 -- run the agent end-to-end and require: clean run on ALL instances AND
# at least one resolved (target #1314). Uses the real model (Ollama) + Docker.
#
# Prereqs: `ollama serve` running with the dev model pulled; Docker Desktop up;
# .cache/repos/validator present (Stage 1). First run is slow (model + modules).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== gate-4: agent end-to-end ==="
python eval/run_eval.py --gate4 "$@"
