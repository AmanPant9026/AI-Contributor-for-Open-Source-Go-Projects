#!/usr/bin/env bash
set -uo pipefail

SANDBOX_IMAGE="${SANDBOX_IMAGE:-go-issue-agent-sandbox:go1.22}"
fail() { echo "FAIL: $1"; exit 1; }
ok()   { echo "ok:   $1"; }

echo "=== gate-0: environment & sandbox check ==="

# 1) docker present + running
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"
docker info >/dev/null 2>&1 || fail "docker daemon not reachable (is Docker running?)"
ok "docker present and running"

# 2) pinned sandbox image exists
docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 \
  || fail "image '$SANDBOX_IMAGE' missing — run 'make build-sandbox' first"
ok "sandbox image present: $SANDBOX_IMAGE"

# 3) pinned Go toolchain answers inside the image
GO_VERSION="$(docker run --rm "$SANDBOX_IMAGE" go version 2>/dev/null)" || fail "could not run go in image"
echo "      $GO_VERSION"
echo "$GO_VERSION" | grep -q "go1.22" || fail "unexpected Go version (wanted go1.22.x): $GO_VERSION"
ok "pinned Go toolchain verified"

# 4) golangci-lint present in image
docker run --rm "$SANDBOX_IMAGE" golangci-lint version >/dev/null 2>&1 \
  || fail "golangci-lint missing in image"
ok "golangci-lint present in image"

# 5) local model reachable + answering
python -m go_issue_agent.llm.client --ping \
  || fail "LLM ping failed (is 'ollama serve' up and the model pulled?)"
ok "local model reachable"

# 6) sandbox can run Go against a real checkout and tell pass from fail
python scripts/stage0_smoke.py \
  || fail "sandbox build/checkout smoke failed"
ok "sandbox smoke passed (pass + fail both detected)"

echo ""
echo "PASSED: gate-0 is green. Lock the checkpoint:"
echo "    git add -A && git commit -m 'stage 0: bedrock' && git tag gate-0"
