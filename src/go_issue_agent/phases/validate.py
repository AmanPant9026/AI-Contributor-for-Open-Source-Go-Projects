"""Phase 5 -- validate a candidate fix without the hidden gold test.

Signals the agent is ALLOWED to use: does it compile, is it vet-clean, and does
the agent's OWN reproduction test pass. (The real resolution verdict comes later
from the Stage-2 harness running the hidden gold test.) Docker-backed via
go_tools, so the agent injects a fake validate_fn in unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..tools import go_tools
from .repair import repro_test_name

_REPRO_FILE = "zz_agent_repro_test.go"


@dataclass
class ValidationResult:
    ok: bool
    stage: str        # "build" | "vet" | "repro" | "tests" | "all"
    detail: str = ""  # error tail, fed back to the model on failure


def validate(repo_dir: str | Path, repro_code: str, *,
             run_existing_tests: bool = False) -> ValidationResult:
    repo_dir = Path(repo_dir)
    (repo_dir / _REPRO_FILE).write_text(repro_code, encoding="utf-8")

    b = go_tools.go_build(repo_dir, "./...")
    if not b.ok:
        return ValidationResult(False, "build", b.tail(800))
    v = go_tools.go_vet(repo_dir, "./...")
    if not v.ok:
        return ValidationResult(False, "vet", v.tail(800))
    name = repro_test_name(repro_code)
    r = go_tools.go_test(repo_dir, [name])
    if not r.ok:
        return ValidationResult(False, "repro", r.tail(800))
    if run_existing_tests:
        t = go_tools.go_test(repo_dir, None)
        if not t.ok:
            return ValidationResult(False, "tests", t.tail(800))
    return ValidationResult(True, "all")
