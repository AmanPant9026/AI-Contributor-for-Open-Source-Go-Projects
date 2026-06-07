#!/usr/bin/env python
"""stage3_smoke.py -- eyeball every Stage 3 tool against the REAL validator clone.

Unlike gate-3 (unit tests on a tiny fixture), this runs the tools on your actual
.cache/repos/validator checkout and prints human-readable output, including a
LIVE Docker run of go_tools (the one thing the unit tests stub). Read the output
top to bottom; every section says what "good" looks like.

Usage:  python scripts/stage3_smoke.py
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from go_issue_agent.tools import fileio, read_span, search_code, apply_patch, go_tools  # noqa: E402
from go_issue_agent.indexing import ast_nav, repo_map  # noqa: E402

REPO = ROOT / ".cache" / "repos" / "validator"
TASKS = ROOT / "eval" / "tasks"


def hr(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def main() -> int:
    if not (REPO / ".git").exists():
        print(f"!! No validator clone at {REPO}.")
        print("   Run your Stage-1 env setup first (it creates .cache/repos/validator).")
        return 1

    # ---------------------------------------------------------------- repo_map
    hr("1) repo_map -- ranked table of contents (library files should rank top)")
    rm = repo_map.build_repo_map(REPO, budget_tokens=1500)
    for fi in rm.ranked_files[:10]:
        print(f"  {fi.rank:.4f}  {fi.path:32}  ({len(fi.symbols)} symbols)")
    leaked = [f.path for f in rm.ranked_files if f.path.startswith(("_", "."))]
    print(f"\n  GOOD if: errors.go / validator*.go near the top, and NO _examples here.")
    print(f"  _-prefixed files leaked? -> {leaked or 'NONE (correct)'}")
    rm2 = repo_map.build_repo_map(REPO, budget_tokens=1500)
    print(f"  deterministic across two builds? -> {rm.skeleton == rm2.skeleton}")
    print("\n  --- first 12 skeleton lines (signatures only, no bodies) ---")
    for ln in rm.skeleton.splitlines()[:12]:
        print("   " + ln)

    # ---------------------------------------------------------------- ast_nav
    hr("2) ast_nav -- parse the big real baked_in.go")
    bi = REPO / "baked_in.go"
    if bi.exists():
        syms = ast_nav.parse_file(bi)
        print(f"  {bi.name}: {len(bi.read_text().splitlines()):,} lines -> {len(syms)} symbols")
        pc = [s for s in syms if "ostcode" in s.name]
        for s in pc:
            print(f"   [{s.kind}] L{s.start_line}-{s.end_line}: {s.signature}")
        print("  GOOD if: ~190+ symbols and the two postcode functions show real line ranges.")

    # ---------------------------------------------------------------- search_code
    hr("3) search_code -- find a symbol (reports which backend it used)")
    backend = "ripgrep" if shutil.which("rg") else "python-fallback"
    hits = search_code.search_code(REPO, r"postcodeRegexInit")
    print(f"  backend: {backend}")
    for h in hits[:6]:
        print(f"   {h.path}:{h.line}: {h.text.strip()[:64]}")
    print("  GOOD if: hits in baked_in.go and postcode_regexes.go with correct line numbers.")

    # ---------------------------------------------------------------- read_span
    hr("4) read_span -- read just the function lines (cheap, numbered)")
    defs = ast_nav.find_definitions(REPO, "isPostcodeByIso3166Alpha2Field")
    if defs:
        rel, sym = defs[0]
        print(read_span.read_span(REPO, rel, sym.start_line, min(sym.start_line + 8, sym.end_line)))
        print("  GOOD if: you see the function header and first lines, with line numbers.")

    # ---------------------------------------------------------------- apply_patch (real)
    hr("5) apply_patch -- apply a REAL gold fix.patch onto its REAL base commit")
    inst_dir = TASKS / "validator-1314"
    if (inst_dir / "instance.json").exists():
        inst = json.loads((inst_dir / "instance.json").read_text())
        base = inst["base_commit"]
        fixp = (inst_dir / "fix.patch").read_text()
        head0 = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        for args in (["checkout", "--force", "--quiet", base],
                     ["reset", "--hard", "--quiet", base], ["clean", "-fdq"]):
            subprocess.run(["git", "-C", str(REPO), *args], check=True)
        before = (REPO / "baked_in.go").read_text().count("postcodeRegexInit.Do(initPostcodes)")
        res = apply_patch.apply_patch(REPO, fixp)
        after = (REPO / "baked_in.go").read_text().count("postcodeRegexInit.Do(initPostcodes)")
        print(f"  apply: applied={res.applied} method={res.method}")
        print(f"  init-call count {before} -> {after} (the patch adds exactly 1)")
        print(f"  re-apply same patch (should fail): {apply_patch.apply_patch(REPO, fixp).applied}")
        print(f"  garbage diff -> {apply_patch.apply_patch(REPO, 'nonsense').method}; "
              f"empty diff noop -> {apply_patch.apply_patch(REPO, '  ').empty}")
        # restore
        subprocess.run(["git", "-C", str(REPO), "checkout", "--force", "--quiet", head0 or "HEAD"])
        subprocess.run(["git", "-C", str(REPO), "reset", "--hard", "--quiet", head0 or "HEAD"])
        print("  GOOD if: applied=True (method=git), count 1->2, re-apply False, garbage failed.")
    else:
        print("  (skipped -- eval/tasks/validator-1314 not present)")

    # ---------------------------------------------------------------- go_tools (LIVE Docker)
    hr("6) go_tools -- LIVE sandbox run (the part unit tests stub)")
    if shutil.which("docker") is None:
        print("  (skipped -- docker not found on PATH)")
    else:
        print(f"  building command: {go_tools.build_cmd('.')}")
        print("  running `go build .` in the pinned sandbox (first run may fetch modules)...")
        r = go_tools.go_build(REPO, ".")
        print(f"  exit={r.exit_code} ok={r.ok} ({r.duration_s:.1f}s)")
        if not r.ok:
            print("  --- output tail ---\n  " + r.tail(400).replace("\n", "\n  "))
        print("  GOOD if: ok=True (the library compiles inside the sandbox).")

    print("\n" + "=" * 72)
    print("Smoke complete. If every 'GOOD if' held, Stage 3 is working end-to-end.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
