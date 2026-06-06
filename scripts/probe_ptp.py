#!/usr/bin/env python3
"""Prove whether run_eval actually runs PASS_TO_PASS and folds it into `resolved`.

It stubs out Docker/git (so it's instant and needs no container) and records the
exact `go test` commands the harness *issues*, then checks the decisive thing:
when the PASS_TO_PASS run is forced to FAIL, does `resolved` flip to false?

Usage: python scripts/probe_ptp.py [instance_dir]
       (default: eval/tasks/validator-1444)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
import run_eval  # noqa: E402

inst_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "eval" / "tasks" / "validator-1444"
inst = json.loads((inst_dir / "instance.json").read_text())
ptp = inst["PASS_TO_PASS"]
ftp = inst["FAIL_TO_PASS"]
assert ptp, "this instance has no PASS_TO_PASS to probe"
probe_name = ptp[0]  # a name that appears ONLY in the PASS_TO_PASS run, not the FAIL_TO_PASS run
print(f"instance      : {inst_dir.name}")
print(f"FAIL_TO_PASS  : {ftp}")
print(f"PASS_TO_PASS  : {len(ptp)} tests (probing with '{probe_name}')")
print()


class _R:
    def __init__(self, ok): self.ok = ok


calls = []

# stub the sandbox/git so nothing real runs; record every command issued
run_eval.repo_ops.checkout = lambda *a, **k: None
run_eval._apply_patch = lambda *a, **k: True
run_eval._install_gold_test = lambda d, i, iid: list(i.get("FAIL_TO_PASS", []))

# --- pass 1: everything succeeds ---
run_eval._run = lambda cmd, timeout_s=1200: (calls.append(cmd) or _R(True))
calls.clear()
s_ok = run_eval.evaluate(inst_dir, "gold")
test_runs = [c for c in calls if "go test -run" in c]
ptp_run = [c for c in test_runs if probe_name in c]

print("RESULT 1 — what the harness ISSUES:")
for c in test_runs:
    n = c.count("|") + 1
    kind = "PASS_TO_PASS" if probe_name in c else "FAIL_TO_PASS"
    print(f"   issued a {kind} run with {n} test name(s)")
print(f"   -> a PASS_TO_PASS test run was issued: {bool(ptp_run)}")
print()
print(f"RESULT 2 — all tests pass  -> status={s_ok.status}  resolved={s_ok.resolved}")

# --- pass 2: force ONLY the PASS_TO_PASS run to fail ---
def _break(cmd, timeout_s=1200):
    calls.append(cmd)
    # fail only the test run that contains a PASS_TO_PASS-only name
    return _R(not ("go test -run" in cmd and probe_name in cmd))

run_eval._run = _break
s_bad = run_eval.evaluate(inst_dir, "gold")
print(f"RESULT 3 — PASS_TO_PASS forced to FAIL -> status={s_bad.status}  resolved={s_bad.resolved}")
print()

ok = bool(ptp_run) and s_ok.resolved is True and s_bad.resolved is False and s_bad.status == "unresolved"
if ok:
    print("VERDICT: ✅  run_eval DOES run PASS_TO_PASS and folds it into `resolved`.")
    print("         When a PASS_TO_PASS test fails, a bug-fixing candidate is")
    print("         correctly marked `unresolved` (not `resolved`).")
else:
    print("VERDICT: ❌  PASS_TO_PASS is NOT being enforced — this is a real bug.")
    sys.exit(1)
