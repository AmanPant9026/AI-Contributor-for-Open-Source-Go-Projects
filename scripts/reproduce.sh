#!/usr/bin/env bash
#
# reproduce.sh -- reproduce the validator evaluation end-to-end on your machine.
#
#   bash scripts/reproduce.sh                  # validate + run the agent over validator
#   bash scripts/reproduce.sh --validate-only  # only the gold-validation gate (no API cost)
#
# Prerequisites: Docker running, Python 3.11+, and a .env with LLM_MODEL + ANTHROPIC_API_KEY
# (copy .env.example to .env first). The full run uses the model and therefore API credits;
# --validate-only exercises just the sandbox + gold gate and costs nothing.
#
set -euo pipefail

# repo root, regardless of where this is invoked from (handles spaces in the path)
cd "$(dirname "$0")/.."

VALIDATE_ONLY=0
[ "${1:-}" = "--validate-only" ] && VALIDATE_ONLY=1

step() { printf "\n==> %s\n" "$1"; }
die()  { printf "ERROR: %s\n" "$1" >&2; exit 1; }

step "checking prerequisites"
command -v python >/dev/null 2>&1 || die "python not found (need Python 3.11+)"
command -v docker >/dev/null 2>&1 || die "docker not found (the validation sandbox needs Docker)"
docker info >/dev/null 2>&1        || die "Docker is installed but not running -- start Docker Desktop and retry"
[ -f .env ] || die "missing .env -- run: cp .env.example .env  then set LLM_MODEL and ANTHROPIC_API_KEY"
echo "  ok: python, docker (running), .env present"

step "building the pinned Go sandbox image (idempotent)"
make build-sandbox

step "unit suite"
python -m pytest tests/ -q

step "gold-validation gate (validator) -- proves each instance is a real FAIL->PASS bug"
python eval/run_eval.py --validate --prefix validator

if [ "$VALIDATE_ONLY" = "1" ]; then
  printf "\n--validate-only: stopping before the agent run (no API used).\n"
  printf "Run without the flag to score the agent end-to-end.\n"
  exit 0
fi

step "running the agent end-to-end over the validator issues (uses the model + sandbox)"
export LITELLM_LOG="${LITELLM_LOG:-ERROR}"   # silence litellm's transient-retry chatter
python eval/run_eval.py --gate4 --prefix validator

cat <<'DONE'

Done. The agent resolves 2/5 on validator (1314, 1476), with localization recall 1.0 on every
scored instance and no patch submitted that failed its own checks.

Per-instance outputs are in eval/results/agent/:
  <instance>.patch          the verified code change
  <instance>.pr.md          the generated PR title + body
  <instance>.repro_test.go  the reproduction the agent wrote
  <instance>.trace.json     the decision trace

To open a draft PR from a verified fix:
  python eval/open_pr.py validator --instance validator-1476 --fork <your-github-username>            # dry run
  python eval/open_pr.py validator --instance validator-1476 --fork <your-github-username> --confirm   # opens it
DONE
