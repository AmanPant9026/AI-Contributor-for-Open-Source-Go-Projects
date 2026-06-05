#!/usr/bin/env bash
set -uo pipefail
SANDBOX_IMAGE="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"
fail(){ echo "FAIL: $1"; exit 1; }
ok(){ echo "ok:   $1"; }

echo "=== gate-0: environment & sandbox check ==="
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"
docker info >/dev/null 2>&1 || fail "docker daemon not reachable (is Docker running?)"
ok "docker present and running"

docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 || fail "image '$SANDBOX_IMAGE' missing — run 'make build-sandbox'"
ok "sandbox image present: $SANDBOX_IMAGE"

GO_VERSION="$(docker run --rm "$SANDBOX_IMAGE" go version 2>/dev/null)" || fail "could not run go in image"
echo "      $GO_VERSION"
echo "$GO_VERSION" | grep -q "go1.24" || fail "unexpected Go version (wanted go1.24.x): $GO_VERSION"
ok "pinned Go toolchain verified"

docker run --rm "$SANDBOX_IMAGE" golangci-lint version >/dev/null 2>&1 || fail "golangci-lint missing in image"
ok "golangci-lint present in image"

python -m go_issue_agent.llm.client --ping || fail "LLM ping failed (is 'ollama serve' up and the model pulled?)"
ok "local model reachable"

python scripts/stage0_smoke.py || fail "sandbox build/checkout smoke failed"
ok "sandbox smoke passed (pass + fail both detected)"

echo ""
echo "PASSED: gate-0 is green."
