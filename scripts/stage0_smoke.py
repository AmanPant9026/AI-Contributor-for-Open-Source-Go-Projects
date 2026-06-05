"""Stage 0 smoke test: prove the pinned sandbox can run Go against a REAL checkout
and correctly tell a passing build from a failing one. No LLM involved."""
from __future__ import annotations
import sys

from go_issue_agent.sandbox.repo import ensure_clone, checkout, current_commit
from go_issue_agent.sandbox.runner import run_in_sandbox

CLONE_URL = "https://github.com/go-playground/validator.git"
NAME = "validator"
BASE_REF = "v10.24.0"  # the pre-fix base for our #1314 ground-truth instance


def section(msg: str) -> None:
    print(f"\n=== {msg} ===")


def main() -> int:
    section(f"clone + checkout {NAME}@{BASE_REF}")
    repo = ensure_clone(CLONE_URL, NAME)
    checkout(repo, BASE_REF)
    print(f"HEAD = {current_commit(repo)}")

    section("PASS case: `go build ./...` on a clean checkout (expect exit 0)")
    clean = run_in_sandbox(repo, "go build ./...")
    print(f"exit={clean.exit_code}  duration={clean.duration_s:.1f}s")
    if not clean.ok:
        print("UNEXPECTED: clean build failed — sandbox/toolchain problem:")
        print(clean.tail())
        return 1
    print("OK: clean checkout builds.")

    section("FAIL case: corrupt a source file, rebuild (expect non-zero exit)")
    victim = repo / "baked_in.go"
    original = victim.read_text()
    try:
        victim.write_text(original + "\nthis is definitely not valid go\n")
        broken = run_in_sandbox(repo, "go build ./...")
        print(f"exit={broken.exit_code}")
        if broken.ok:
            print("UNEXPECTED: corrupted build still passed — we cannot detect failures!")
            return 1
        print("OK: corrupted checkout fails as expected (we can detect failure).")
    finally:
        victim.write_text(original)
        checkout(repo, BASE_REF)  # hard reset to a clean tree

    section("RESULT")
    print("Stage 0 sandbox smoke PASSED: pass detected, fail detected, tree restored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
