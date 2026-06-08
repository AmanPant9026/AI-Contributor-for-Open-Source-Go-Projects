#!/usr/bin/env python3
"""Stage 2 -- the ruler. Score a candidate patch against the frozen answer key.

Folder layout (one folder per bug):
    eval/tasks/validator-<id>/
        instance.json     (the answer key)
        fix.patch         (the gold code fix)
        test.patch        (the gold test, Flavor A)  -- or --
        repro_test.go     (the authored test, Flavor B; takes precedence)

For each instance we:
  1. check out the repo at base_commit (throwaway working copy in .cache),
  2. install the *gold* test (repro_test.go if present, else test.patch),
  3. apply the *candidate* code patch (gold / empty / -- later -- the agent's),
  4. run build / vet / gofmt gates and the FAIL_TO_PASS (and PASS_TO_PASS) tests
     inside the pinned Docker sandbox,
  5. score it with eval/metrics.py.

This is exactly what scripts/verify_gt.sh does by hand, generalized to score
ANY candidate (not just the gold one) and to emit the metrics table.

gate-2 (the ruler's self-check):  python eval/run_eval.py --gate
  -> feed every instance its GOLD patch  -> all must be resolved
  -> feed every instance an EMPTY patch  -> none may be resolved
If the ruler can't tell a correct fix from no fix, it isn't a ruler.

Usage:
  python eval/run_eval.py --gate                 # gate-2 self-check (gold vs empty)
  python eval/run_eval.py --candidate gold       # score gold patches, write baseline
  python eval/run_eval.py --candidate empty      # score empty patches
  python eval/run_eval.py --only 1314 1284       # restrict to some instances
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from go_issue_agent.config import settings               # noqa: E402
from go_issue_agent.sandbox import repo as repo_ops       # noqa: E402
from go_issue_agent.sandbox.runner import run_in_sandbox  # noqa: E402

sys.path.insert(0, str(ROOT / "eval"))
import metrics  # noqa: E402

TASKS = ROOT / "eval" / "tasks"
RESULTS = ROOT / "eval" / "results"
REPO_DIR = ROOT / ".cache" / "repos" / "validator"
GOMOD_CACHE = ROOT / ".cache" / "gomod"   # persistent module cache (kills re-downloads)
_FUNC_RE = re.compile(r"func\s+(Test[A-Za-z0-9_]+)")


# ---------------------------------------------------------------- git/patch helpers

def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True)


def _apply_patch(patch_text: str) -> bool:
    """Apply a unified diff to the checkout. Mirrors verify_gt.sh's tolerant apply.
    An empty patch (the 'empty' candidate) is a successful no-op."""
    if not patch_text.strip():
        return True
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as tf:
        tf.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
        pf = tf.name
    try:
        if _git(["apply", "--recount", "--ignore-whitespace", pf]).returncode == 0:
            return True
        return subprocess.run(["patch", "-d", str(REPO_DIR), "-p1", "--fuzz=3", "-i", pf],
                              capture_output=True, text=True).returncode == 0
    finally:
        Path(pf).unlink(missing_ok=True)


def _detect_test_names(go_file: Path) -> list[str]:
    return sorted(set(_FUNC_RE.findall(go_file.read_text())))


# ---------------------------------------------------------------- sandbox helpers

def _run(cmd: str, timeout_s: int = 1200):
    """Run one command in the pinned sandbox, with a persistent Go module cache mounted."""
    return run_in_sandbox(
        REPO_DIR, cmd, image=settings.sandbox_image, timeout_s=timeout_s,
        extra_mounts=[(str(GOMOD_CACHE), "/go/pkg/mod")],
    )


def _tests_pass(test_names: list[str]) -> bool:
    if not test_names:
        return True
    regex = "^(" + "|".join(test_names) + ")$"
    return _run(f"go test -run '{regex}' ./...").ok


# ---------------------------------------------------------------- core

def _install_gold_test(inst_dir: Path, inst: dict, iid: str) -> list[str]:
    """Put the gold/authored test in the checkout; return its FAIL_TO_PASS names.
    An authored repro_test.go (Flavor B) takes precedence and drives its own names."""
    repro = inst_dir / "repro_test.go"
    if repro.exists():
        shutil.copy(repro, REPO_DIR / f"zz_v{iid}_repro_test.go")
        return _detect_test_names(repro)
    test_patch = inst.get("test_patch", "")
    if test_patch.strip() and not _apply_patch(test_patch):
        raise RuntimeError(f"{iid}: could not apply gold test_patch")
    return list(inst.get("FAIL_TO_PASS", []))


def _run_resolved(inst_dir: Path, inst: dict, iid: str) -> tuple[bool, bool, bool]:
    """Install the gold test and run FAIL_TO_PASS / PASS_TO_PASS.
    Returns (ftp_passed, ptp_passed, resolved)."""
    ftp = _install_gold_test(inst_dir, inst, iid)
    ftp_passed = _tests_pass(ftp)
    ptp_passed = _tests_pass(list(inst.get("PASS_TO_PASS", [])))
    return ftp_passed, ptp_passed, (ftp_passed and ptp_passed)


def evaluate(inst_dir: Path, candidate: str, cand_diff_override: str | None = None) -> metrics.InstanceScore:
    inst = json.loads((inst_dir / "instance.json").read_text())
    iid = inst_dir.name.replace("validator-", "")
    instance_id = inst.get("instance_id", inst_dir.name)
    gold_patch = inst.get("patch", "")
    cand_diff = (gold_patch if candidate == "gold"
                 else "" if candidate == "empty"
                 else (cand_diff_override or ""))
    gold_files = metrics.files_in_diff(gold_patch)
    cand_files = metrics.files_in_diff(cand_diff)
    sim = metrics.diff_similarity(cand_diff, gold_patch)
    has_ptp = bool(inst.get("PASS_TO_PASS"))
    ptp_note = "" if has_ptp else "no PASS_TO_PASS recorded"

    repo_ops.checkout(REPO_DIR, inst["base_commit"])

    # ---- case 1: NO-OP (empty candidate) -- nothing to judge ----
    # Gates and localization are n/a (there is no candidate code). We still RUN
    # the failing test, so `resolved` is a real measurement (it must be False:
    # with no change, the bug is still present).
    if not cand_diff.strip():
        ftp_passed, ptp_passed, resolved = _run_resolved(inst_dir, inst, iid)
        repo_ops.checkout(REPO_DIR, inst["base_commit"])
        status = metrics.RESOLVED if resolved else metrics.NOOP
        return metrics.InstanceScore(
            instance_id, candidate, status=status, resolved=resolved,
            ftp_passed=ftp_passed, ptp_passed=ptp_passed,
            recall=None, precision=None, build_ok=None, vet_ok=None, fmt_ok=None,
            diff_similarity=sim, note=("no patch applied (no-op); " + ptp_note).strip("; "),
            cand_files=[], gold_files=sorted(gold_files))

    # ---- case 2: APPLY FAILED -- a non-empty diff that won't apply ----
    # The tree is unchanged, so gates would judge the base, not the candidate ->
    # n/a. Localization is still meaningful from the diff's INTENT (which files
    # it claims to touch), so we report it.
    if not _apply_patch(cand_diff):
        recall, precision = metrics.localization(cand_files, gold_files)
        repo_ops.checkout(REPO_DIR, inst["base_commit"])
        return metrics.InstanceScore(
            instance_id, candidate, status=metrics.APPLY_FAILED, resolved=False,
            ftp_passed=False, ptp_passed=False, recall=recall, precision=precision,
            build_ok=None, vet_ok=None, fmt_ok=None, diff_similarity=sim,
            note="candidate patch did not apply",
            cand_files=sorted(cand_files), gold_files=sorted(gold_files))

    # ---- case 3: APPLIED -- judge the candidate's code, then the outcome ----
    # Gates run BEFORE the gold test is installed, so they measure the
    # candidate's own code quality (against the project's real tests), not
    # whether it satisfies our hidden gold test (that is `resolved`).
    build_ok = _run("go build ./...").ok
    vet_ok = _run("go vet ./...").ok
    go_cand = [f for f in sorted(cand_files) if f.endswith(".go")]
    if go_cand:
        quoted = " ".join(shlex.quote(f) for f in go_cand)
        fmt_ok = _run(f'test -z "$(gofmt -l {quoted} 2>/dev/null)"').ok
    else:
        fmt_ok = True  # candidate changed no Go files -> nothing to format

    ftp_passed, ptp_passed, resolved = _run_resolved(inst_dir, inst, iid)
    recall, precision = metrics.localization(cand_files, gold_files)
    repo_ops.checkout(REPO_DIR, inst["base_commit"])  # cleanup

    status = metrics.RESOLVED if resolved else metrics.UNRESOLVED
    return metrics.InstanceScore(
        instance_id, candidate, status=status, resolved=resolved,
        ftp_passed=ftp_passed, ptp_passed=ptp_passed, recall=recall, precision=precision,
        build_ok=build_ok, vet_ok=vet_ok, fmt_ok=fmt_ok, diff_similarity=sim,
        note=ptp_note, cand_files=sorted(cand_files), gold_files=sorted(gold_files))


def load_instance_dirs(only: list[str] | None) -> list[Path]:
    dirs = sorted(p.parent for p in TASKS.glob("validator-*/instance.json"))
    if only:
        keep = set(only)
        dirs = [d for d in dirs if d.name.replace("validator-", "") in keep]
    return dirs


def score_all(candidate: str, only: list[str] | None) -> list[metrics.InstanceScore]:
    scores = []
    for d in load_instance_dirs(only):
        print(f"  [{candidate}] {d.name} ...", flush=True)
        scores.append(evaluate(d, candidate))
    return scores


def score_agent(only: list[str] | None) -> list[metrics.InstanceScore]:
    """Run the REAL agent on each instance, then score its code-only patch with
    the same ruler used for gold/empty. Needs Ollama + Docker. Per-instance
    errors are captured (status='error') so one failure doesn't abort the gate."""
    from go_issue_agent.agent import run_agent          # lazy: avoids importing llm for gate-2
    from go_issue_agent.llm.client import LLMClient

    llm = LLMClient(max_tokens=1024)
    agent_out = RESULTS / "agent"
    agent_out.mkdir(parents=True, exist_ok=True)
    scores: list[metrics.InstanceScore] = []
    for d in load_instance_dirs(only):
        inst = json.loads((d / "instance.json").read_text())
        instance_id = inst.get("instance_id", d.name)
        print(f"  [agent] {d.name}: running agent (this calls the model + sandbox) ...", flush=True)
        try:
            repo_ops.checkout(REPO_DIR, inst["base_commit"])
            res = run_agent(inst["problem_statement"], REPO_DIR,
                            llm=llm, base_ref=inst["base_commit"],
                            on_log=lambda m, n=d.name: print(f"      [{n}] {m}", flush=True))
            (agent_out / f"{d.name}.patch").write_text(res.code_patch)
            (agent_out / f"{d.name}.pr.md").write_text(f"# {res.pr_title}\n\n{res.pr_body}\n")
            (agent_out / f"{d.name}.repro_test.go").write_text(res.repro_code)
            (agent_out / f"{d.name}.trace.json").write_text(json.dumps(res.trace, indent=2))
            if not res.code_patch.strip() and res.attempt_patch.strip():
                (agent_out / f"{d.name}.attempt.patch").write_text(res.attempt_patch)
            sc = evaluate(d, "agent", cand_diff_override=res.code_patch)
            sc.note = (sc.note + f"; agent={res.status} attempts={res.attempts}").strip("; ")
            scores.append(sc)
        except Exception as e:  # noqa: BLE001
            print(f"      [{d.name}] ERROR: {e}", flush=True)
            scores.append(metrics.InstanceScore(
                instance_id, "agent", status="error", resolved=False,
                ftp_passed=False, ptp_passed=False, recall=None, precision=None,
                build_ok=None, vet_ok=None, fmt_ok=None, diff_similarity=0.0,
                note=f"agent crashed: {e}", cand_files=[], gold_files=[]))
    return scores


def cmd_gate4(args) -> int:
    dirs = load_instance_dirs(args.only)
    if not dirs:
        print("FAIL: no instances found"); return 1
    print(f"=== gate-4: run the agent end-to-end over {len(dirs)} instance(s) ===")
    print("(this uses the real model via Ollama and the Docker sandbox; first run is slow)")
    scores = score_agent(args.only)
    print("\n" + metrics.format_table(scores))

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "gate4.json").write_text(json.dumps([s.to_dict() for s in scores], indent=2))

    ran = [s for s in scores if s.status != "error"]
    resolved = [s for s in scores if s.resolved]
    target = next((s for s in scores if s.instance_id.endswith("1314")), None)
    print()
    print(f"statuses     : {metrics.status_counts(scores)}")
    print(f"ran clean    : {len(ran)}/{len(scores)} (no crashes)")
    print(f"resolved     : {sum(s.resolved for s in scores)}/{len(scores)} "
          f"(resolution rate {metrics.resolution_rate(scores)})")
    if target is not None:
        print(f"target #1314 : {'RESOLVED' if target.resolved else target.status}")
    print()
    # Gate: ran clean on ALL, and resolved at least one (target 1314 the goal).
    if len(ran) == len(scores) and len(resolved) >= 1:
        print("PASSED: gate-4 -- the agent runs clean on all instances and resolves at least one.")
        return 0
    if len(ran) != len(scores):
        print("FAIL: gate-4 -- the agent crashed on some instance(s) (see rows above).")
    else:
        print("FAIL: gate-4 -- the agent ran clean but resolved none. Inspect eval/results/agent/.")
    return 1


# ---------------------------------------------------------------- CLI

def cmd_gate(args) -> int:
    dirs = load_instance_dirs(args.only)
    if not dirs:
        print("FAIL: no instances found under eval/tasks/validator-*/instance.json"); return 1
    print(f"=== gate-2: ruler self-check over {len(dirs)} instance(s) ===")
    print("--- gold candidates (every one must resolve) ---")
    gold = score_all("gold", args.only)
    print(metrics.format_table(gold))
    print("--- empty candidates (none may resolve) ---")
    empty = score_all("empty", args.only)
    print(metrics.format_table(empty))

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "gate2.json").write_text(json.dumps(
        {"gold": [s.to_dict() for s in gold], "empty": [s.to_dict() for s in empty]}, indent=2))

    gold_all = all(s.resolved for s in gold)
    gold_recall = all(s.recall == 1.0 for s in gold)
    empty_none = not any(s.resolved for s in empty)
    print()
    print(f"gold  statuses: {metrics.status_counts(gold)}")
    print(f"empty statuses: {metrics.status_counts(empty)}")
    print(f"gold resolved : {sum(s.resolved for s in gold)}/{len(gold)}"
          f"   (resolution rate {metrics.resolution_rate(gold)})")
    print(f"gold recall=1 : {sum(1 for s in gold if s.recall == 1.0)}/{len(gold)}")
    print(f"empty resolved: {sum(s.resolved for s in empty)}/{len(empty)} (want 0)")
    print()
    if gold_all and gold_recall and empty_none:
        print("PASSED: gate-2 -- the ruler distinguishes a correct fix from no fix.")
        return 0
    print("FAIL: gate-2 -- ruler is not trustworthy yet (see rows above).")
    return 1


def cmd_candidate(args) -> int:
    scores = score_all(args.candidate, args.only)
    print(metrics.format_table(scores))
    print(f"\nresolution rate: {metrics.resolution_rate(scores)}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / ("baseline.json" if args.candidate == "gold" else f"{args.candidate}.json")
    out.write_text(json.dumps([s.to_dict() for s in scores], indent=2))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 2 eval harness (the ruler) + Stage 4 agent runner.")
    ap.add_argument("--gate", action="store_true", help="run the gate-2 self-check (gold vs empty)")
    ap.add_argument("--gate4", action="store_true", help="run the gate-4 agent end-to-end check")
    ap.add_argument("--candidate", choices=["gold", "empty", "agent"], help="score one candidate kind")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these instance ids")
    args = ap.parse_args()
    if not REPO_DIR.exists():
        print(f"FAIL: validator checkout missing at {REPO_DIR} (run 'make check-env' once)")
        return 1
    if args.gate:
        return cmd_gate(args)
    if args.gate4:
        return cmd_gate4(args)
    if args.candidate == "agent":
        scores = score_agent(args.only)
        print("\n" + metrics.format_table(scores))
        print(f"\nresolution rate: {metrics.resolution_rate(scores)}")
        return 0
    if args.candidate:
        return cmd_candidate(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
