#!/usr/bin/env python
"""Deterministic coverage-localization diagnostic (no LLM, no agent).

For each instance it: checks out the base commit, applies the GOLD test (`test.patch`
— the canonical reproduction), runs that test under Go coverage, and reports:

  - which source files have EXECUTED statements (coverage > 0),
  - whether the gold-fix file is among them,
  - whether the gold-fix file even HAS functions (a data-only file like `regexes.go`
    has none, so coverage cannot see it — but its CALLER will show up), and
  - the neighborhood, so we can see what coverage *would* hand the agent.

This tests the central Stage-5 question empirically before we build anything:
does running the bug's reproduction localize it? It will (logic bugs inside
functions) or it won't (data-only files) — and the report shows which, per instance.

Run from the repo root:   python eval/diagnose_coverage.py
One instance:             python eval/diagnose_coverage.py validator-1476
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TASKS = ROOT / "eval" / "tasks"
REPO_DIR = ROOT / ".cache" / "repos" / "validator"

from go_issue_agent.indexing import ast_nav                  # noqa: E402
from go_issue_agent.sandbox.runner import run_in_sandbox     # noqa: E402
from go_issue_agent.config import settings                   # noqa: E402

_COVER_LINE = re.compile(r"^(.+\.go):\d+\.\d+,\d+\.\d+ \d+ (\d+)$")


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True)


def _reset_clean():
    _git("checkout", "-q", "--", ".")
    _git("clean", "-fdq")


def module_path() -> str:
    try:
        for line in (REPO_DIR / "go.mod").read_text(errors="replace").splitlines():
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except OSError:
        pass
    return ""


def gold_files(task_dir: Path) -> list[str]:
    patch = (task_dir / "fix.patch").read_text(errors="replace")
    fs = [m.group(1).strip() for m in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.M)]
    return [f for f in dict.fromkeys(fs) if f != "/dev/null" and not f.endswith("_test.go")]


def test_funcs(task_dir: Path) -> list[str]:
    patch = (task_dir / "test.patch").read_text(errors="replace")
    return list(dict.fromkeys(re.findall(r"^\+\s*func (Test\w+)", patch, re.M)))


def has_functions(path: str):
    try:
        syms = ast_nav.parse_file(REPO_DIR / path)
        return any(getattr(s, "kind", "") in ("func", "method") for s in syms)
    except Exception:  # noqa: BLE001
        return None


def parse_cover(mod: str):
    cov = REPO_DIR / "cover.out"
    if not cov.exists():
        return set(), False
    prefix = mod + "/" if mod else ""
    totals: dict[str, int] = {}
    for line in cov.read_text(errors="replace").splitlines():
        m = _COVER_LINE.match(line)
        if not m:
            continue
        f, cnt = m.group(1), int(m.group(2))
        if f.startswith(prefix):
            f = f[len(prefix):]
        totals[f] = totals.get(f, 0) + cnt
    return {f for f, c in totals.items() if c > 0}, True


def diagnose(task_dir: Path, mod: str) -> None:
    inst = json.loads((task_dir / "instance.json").read_text())
    gf, tf = gold_files(task_dir), test_funcs(task_dir)
    print(f"\n=== {task_dir.name}  (gold: {gf or '?'}) ===")
    print(f"  gold test funcs : {tf or '(none parsed — running full suite)'}")

    _reset_clean()
    _git("checkout", "-q", inst["base_commit"])
    _reset_clean()
    ap = _git("apply", "--whitespace=nowarn", str(task_dir / "test.patch"))
    if ap.returncode != 0:
        print(f"  [skip] test.patch did not apply on base: {ap.stderr.strip()[:200]}")
        _reset_clean()
        return

    run = "^(" + "|".join(tf) + ")$" if tf else ""
    sel = f"-run '{run}' " if run else ""
    cmd = f"go test {sel}-coverpkg=./... -coverprofile=cover.out ./... 2>&1 | tail -4"
    res = run_in_sandbox(REPO_DIR, cmd, image=settings.sandbox_image, timeout_s=600)

    covered, ok = parse_cover(mod)
    if not ok:
        out = (res.stdout or res.stderr or "").strip()[:400]
        print("  [NO coverage profile produced] — test output:")
        print("    " + out.replace("\n", "\n    "))
        print("    (most likely the gold test does NOT COMPILE on base — e.g. it calls\n"
              "     public methods that the fix introduces, as in a 'new API' bug)")
    else:
        shown = sorted(covered)
        print(f"  covered source files ({len(covered)}):")
        for f in shown[:25]:
            print(f"     {f}")
        if len(shown) > 25:
            print(f"     ... (+{len(shown) - 25} more)")
        for g in gf:
            hf = has_functions(g)
            where = "IN coverage" if g in covered else "NOT in coverage"
            why = "" if hf is None else (
                "  (has functions)" if hf
                else "  (DATA-ONLY: no functions — coverage cannot see it; check its caller above)")
            print(f"  -> gold {g}: {where}{why}")

    (REPO_DIR / "cover.out").unlink(missing_ok=True)
    _reset_clean()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not REPO_DIR.exists():
        print(f"validator checkout not found at {REPO_DIR}")
        return
    mod = module_path()
    orig = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirs = sorted(p.parent for p in TASKS.glob("validator-*/instance.json"))
    if args:
        dirs = [d for d in dirs if d.name in args]
    if not dirs:
        print(f"No instances under {TASKS}")
        return
    print(f"module: {mod or '(unknown)'}")
    try:
        for d in dirs:
            try:
                diagnose(d, mod)
            except Exception as e:  # noqa: BLE001
                print(f"\n=== {d.name} ===\n  ERROR: {e}")
                _reset_clean()
    finally:
        if orig and orig != "HEAD":
            _git("checkout", "-q", orig)
            _reset_clean()
    print("\n(Coverage shows files with EXECUTED statements. A data-only file (regexes.go) "
          "has\n no functions, so it cannot appear — but its CALLER, e.g. baked_in.go, will, "
          "which\n is the neighborhood the agent investigates from. Logic-bug files appear "
          "directly.)")


if __name__ == "__main__":
    main()
