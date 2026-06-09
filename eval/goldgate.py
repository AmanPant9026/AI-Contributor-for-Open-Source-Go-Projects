"""Gold-validation gate -- pure decision logic (the Docker orchestration lives in
run_eval). An imported/harvested instance is only TRUSTED if, in our own sandbox, its
gold patch makes the bug's tests go from failing to passing -- the same firewall SWE-bench
uses (`--predictions_path gold`). This module turns `go test -v` output into a verdict;
run_eval runs the tests and applies the verdict.
"""
from __future__ import annotations

import re

# `    --- PASS: TestFoo (0.00s)`  /  `--- FAIL: TestBar/sub`
_VERDICT = re.compile(r"^\s*--- (PASS|FAIL): ((?:Test|Example|Fuzz|Benchmark)[A-Za-z0-9_/]*)", re.M)


def parse_go_test_v(output: str) -> dict[str, str]:
    """Map test name -> 'PASS' | 'FAIL' from `go test -v` output (last verdict wins)."""
    res: dict[str, str] = {}
    for m in _VERDICT.finditer(output):
        res[m.group(2)] = m.group(1)
    return res


def decide_validation(f2p_at_base: dict[str, str], f2p_after_fix: dict[str, str],
                      p2p_after_fix: dict[str, str]) -> tuple[bool, str, list[str], list[str]]:
    """Decide whether an instance is a valid FAIL->PASS task.

    A valid instance requires EVERY provisional FAIL_TO_PASS test to (a) FAIL at base
    (proving the test actually catches the bug) and (b) PASS once the gold fix is applied.
    The PASS_TO_PASS set returned is the regression-guard tests that pass after the fix.

    Returns (keep, reason, confirmed_FAIL_TO_PASS, confirmed_PASS_TO_PASS).
    """
    f2p = sorted(set(f2p_at_base) | set(f2p_after_fix))
    if not f2p:
        return False, "no FAIL_TO_PASS tests ran", [], []
    absent_at_base = [t for t in f2p if t not in f2p_at_base]
    if absent_at_base:                       # didn't compile/run at base -> usually a NEW feature
        return False, (f"F2P did not run at base (compile error or test absent -- often a new "
                       f"feature, not a bug): {absent_at_base}"), [], []
    pass_at_base = [t for t in f2p if f2p_at_base.get(t) == "PASS"]
    if pass_at_base:                         # ran but passed -> the test doesn't catch the bug
        return False, f"F2P passes at base (test does not catch the bug): {pass_at_base}", [], []
    fail_after = [t for t in f2p if f2p_after_fix.get(t) != "PASS"]
    if fail_after:
        return False, f"gold fix does not make F2P pass: {fail_after}", [], []
    p2p = sorted(t for t, v in p2p_after_fix.items() if v == "PASS")
    return True, "validated", f2p, p2p
