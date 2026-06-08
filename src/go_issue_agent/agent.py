"""The agent orchestrator -- the localize -> repair -> validate spine.

Wires the Stage-3 tools to the LLM with two BOUNDED loops (context-gather in
phase 3, repair in phase 6). Both the model (`llm`) and the Docker-backed
validation (`validate_fn`) are injectable, so the whole spine is unit-testable
with a fake model and a fake validator -- no Ollama, no Docker.

The agent only ever sees `problem_statement` + the code at `base_commit`; it never
sees the gold fix or gold tests. Its own reproduction test is a self-check signal
and is excluded from the final (code-only) patch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .llm.client import LLMClient
from .sandbox import repo as repo_ops
from .phases import context as context_phase
from .phases import evidence as evidence_phase
from .phases import finalize, localize, repair, validate as validate_phase
from .phases.evidence import Evidence
from .phases.validate import ValidationResult
from .tools import go_tools
from . import tracing

# A validate_fn takes (repo_dir, repro_code) and returns a ValidationResult.
ValidateFn = Callable[[Path, str], ValidationResult]
# An evidence_fn runs the repro and returns execution evidence for localization.
EvidenceFn = Callable[[Path, str], Evidence]


@dataclass
class AgentResult:
    status: str                      # resolved_internally | gave_up | abstained | no_repro | no_edits
    code_patch: str                  # code-only unified diff (the submission)
    repro_code: str                  # the agent's reproduction test (scratch, not submitted)
    pr_title: str = ""
    pr_body: str = ""
    attempts: int = 0
    internal_ok: bool = False        # did the agent's own repro pass?
    attempt_patch: str = ""          # best/last attempted diff, even if not submitted (debug visibility)
    log: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)   # structured decision/tool-call trace


def _repo_go_files(repo_dir: Path) -> list[str]:
    root = Path(repo_dir)
    return [str(p.relative_to(root)) for p in root.rglob("*.go") if ".git" not in p.parts]


def _gather_evidence(repo_dir: Path, repro_code: str, base_ref: str) -> Evidence:
    """Run the validated repro under coverage on the UNPATCHED base and mine the run for
    where the bug lives: executed files (coverage), plus any compiler error or panic trace.
    Docker-backed, so used only in real mode -- tests inject their own evidence_fn."""
    repo_ops.checkout(repo_dir, base_ref)
    repro_file = repo_dir / "zz_agent_repro_test.go"
    repro_file.write_text(repro_code, encoding="utf-8")
    res = go_tools.go_test_cover(repo_dir, [repair.repro_test_name(repro_code)])
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    prof = repo_dir / "cover.out"
    covered = (evidence_phase.parse_coverage(prof.read_text(errors="replace"),
                                             evidence_phase.module_path(repo_dir))
               if prof.exists() else [])
    ev = Evidence(
        covered=covered,
        traced=evidence_phase.parse_trace_files(out, _repo_go_files(repo_dir)),
        compiled_err=evidence_phase.parse_compiler_files(out),
    )
    prof.unlink(missing_ok=True)
    repro_file.unlink(missing_ok=True)
    return ev


def run_agent(problem_statement: str, repo_dir: str | Path, *, llm: LLMClient,
              base_ref: str | None = None, max_context_reads: int = 5,
              max_repair_attempts: int = 3, max_repro_attempts: int = 3,
              validate_fn: ValidateFn | None = None,
              reproduces_fn: Callable[[Path, str], bool] | None = None,
              evidence_fn: EvidenceFn | None = None,
              run_existing_tests: bool = False, submit_unvalidated: bool = False,
              on_log: Callable[[str], None] | None = None) -> AgentResult:
    repo_dir = Path(repo_dir)
    base_ref = base_ref or repo_ops.current_commit(repo_dir)
    log: list[str] = []
    tracer = tracing.Tracer()
    if hasattr(llm, "attach_tracer"):
        llm.attach_tracer(tracer)   # every model call is now recorded with cache hit/miss

    def say(msg: str) -> None:
        log.append(msg)
        if on_log:
            on_log(msg)

    _real_validate = validate_fn is None
    if validate_fn is None:
        def validate_fn(rd: Path, code: str) -> ValidationResult:  # type: ignore[misc]
            return validate_phase.validate(rd, code, run_existing_tests=run_existing_tests)
        if reproduces_fn is None:
            # A real reproduction test must FAIL on the unpatched base (build+vet ok, repro fails).
            def reproduces_fn(rd: Path, code: str) -> bool:  # type: ignore[misc]
                repo_ops.checkout(rd, base_ref)
                r = validate_phase.validate(rd, code, run_existing_tests=False)
                return (not r.ok) and r.stage == "repro"
    elif reproduces_fn is None:
        # a fake validate was injected (tests): treat the repro as valid unless overridden
        reproduces_fn = lambda rd, code: True  # noqa: E731

    if evidence_fn is None:
        # Real mode: run the repro under coverage and mine it. Test mode (fake validate):
        # no execution evidence, so localization falls back to the lexical+PageRank ranking.
        evidence_fn = ((lambda rd, code: _gather_evidence(rd, code, base_ref)) if _real_validate
                       else (lambda rd, code: Evidence()))

    # Phase 2: localize
    loc = localize.localize(repo_dir, problem_statement)
    say(f"localize: terms={loc.terms[:6]} candidates={loc.candidates[:5]}")
    tracer.decision("localize", terms=loc.terms[:8], candidates=loc.candidates[:8])

    # Phase 3: gather context (bounded loop)
    ctx = context_phase.gather(llm, repo_dir, loc, problem_statement,
                               max_reads=max_context_reads)
    say(f"context: actions={ctx.actions}")
    tracer.decision("context", actions=ctx.actions, primary=ctx.primary_file)

    # Phase 4a: the agent's own reproduction test -- and CHECK it actually reproduces the
    # bug (fails on the unpatched base). A test that passes on buggy code is invalid and would
    # wrongly judge fixes, so we regenerate with feedback until it reproduces (or give up).
    repro_code = ""
    repro_valid = False
    for r_attempt in range(1, max_repro_attempts + 1):
        fb = "" if r_attempt == 1 else (
            "Your previous test PASSED on the current unfixed code, so it does NOT reproduce "
            "the bug. It must FAIL now. Re-read the report and assert the behavior that SHOULD "
            "happen (currently broken) -- e.g. a valid input being accepted, not rejected.")
        repro_code = repair.propose_repro(llm, problem_statement, ctx.text, feedback=fb)
        if reproduces_fn(repo_dir, repro_code):
            repro_valid = True
            say(f"repro: {len(repro_code)} chars, test={repair.repro_test_name(repro_code)} "
                f"(reproduces the bug on attempt {r_attempt})")
            break
        say(f"repro attempt {r_attempt}: does NOT fail on base (invalid) -- regenerating")
    tracer.decision("repro", valid=repro_valid, attempts=r_attempt,
                    test=repair.repro_test_name(repro_code) if repro_valid else None)

    if not repro_valid:
        # We could not build a test that reproduces the bug, so we cannot VERIFY any fix.
        # Do no harm: abstain rather than submit something unverifiable.
        say("repro: could not produce a reproducing test -> abstaining (cannot verify a fix)")
        tracer.decision("finalize", status="no_repro", patch_bytes=0)
        return AgentResult(status="no_repro", code_patch="", repro_code=repro_code,
                           attempts=0, internal_ok=False,
                           log=log, trace=tracer.to_dict())

    # Phase 4c: execution-evidence localization. Run the validated repro under coverage and
    # mine the run (compiler error / panic trace / coverage) for the handful of files the bug
    # actually touches. Coverage does the NARROWING; the model does the ORDERING of that short
    # list (lexical ranking gets it wrong when the buggy file is a quiet data/util file the
    # issue text doesn't name). With no execution evidence we fall back to the original
    # behavior: every attempt targets the context-phase primary file.
    ev = evidence_fn(repo_dir, repro_code)
    tracer.tool_call("coverage", covered=len(ev.covered), traced=len(ev.traced),
                     compiled_err=len(ev.compiled_err))
    if ev.any():
        covered_ranked = evidence_phase.rerank(loc.candidates, ev)[:8]    # coverage-narrowed
        targets = repair.rank_suspects(llm, problem_statement, ctx.text, covered_ranked)[:3]
        per_file = 2          # attempts per file, with feedback (recover a fixable build error)
        say(f"evidence: covered={len(ev.covered)} traced={ev.traced[:2]} "
            f"compile_err={ev.compiled_err[:2]} -> model-ranked targets={targets} (x{per_file})")
    else:
        primary = ctx.primary_file or (loc.candidates[0] if loc.candidates else None)
        targets = [primary] if primary else []
        per_file = max_repair_attempts                                   # original behavior
        say(f"evidence: none from repro -> lexical+PageRank ranking, target={primary} (x{per_file})")
    tracer.decision("evidence", has_evidence=ev.any(), covered=len(ev.covered),
                    targets=targets, per_file=per_file)

    # Phase 4b/5/6: for each target file, up to `per_file` propose -> apply -> validate
    # attempts; feedback carries WITHIN a file (so a build error gets fixed) and resets between
    # files. Submit ONLY a fix that passes build + vet + the repro; otherwise abstain.
    internal_ok = False
    attempts = 0
    ever_applied = False
    verified_patch: str | None = None     # build + vet + repro ALL pass -> the only thing we submit
    last_applied_patch = ""                # most recent applied diff (for debug visibility only)
    from . import edits as edits_mod
    for target in targets:
        if verified_patch:
            break
        feedback = ""                                  # fresh per file
        # load the target file's exact text so SEARCH blocks match it verbatim
        excerpt = context_phase.file_excerpt(repo_dir, target, loc.terms) if ev.any() else ""
        ctx_text = ctx.text + (f"\n\n{excerpt}" if excerpt else "")
        for _ in range(per_file):
            attempts += 1
            repo_ops.checkout(repo_dir, base_ref)      # independent attempts
            tracer.tool_call("checkout", ref=str(base_ref)[:12])
            edits = repair.propose_fix(llm, problem_statement, ctx_text, feedback, target_file=target)
            if not edits:
                say(f"attempt {attempts} (target {target}): model produced no edits")
                feedback = ("You produced no valid search/replace blocks. Output at least one "
                            "block using the <<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers.")
                tracer.decision("repair_attempt", n=attempts, target=target, outcome="no_edits")
                continue
            applied = edits_mod.apply_edits(repo_dir, edits, default_target=target)
            tracer.tool_call("apply_edits", target=target, applied=applied.applied,
                             files=applied.changed_files)
            if applied.applied == 0:                   # nothing changed -> retry with feedback
                say(f"attempt {attempts} (target {target}): apply failed: {applied.failures}")
                feedback = "; ".join(applied.failures)
                tracer.decision("repair_attempt", n=attempts, target=target, outcome="apply_failed")
                continue
            ever_applied = True
            patch_now = finalize.code_only_diff(repo_dir)
            if patch_now.strip():
                last_applied_patch = patch_now
            res = validate_fn(repo_dir, repro_code)
            tracer.tool_call("validate", target=target, stage=res.stage, ok=res.ok)
            say(f"attempt {attempts} (target {target}): edited {applied.changed_files} -> "
                f"validate {res.stage} ok={res.ok}")
            tracer.decision("repair_attempt", n=attempts, target=target,
                            outcome=("verified" if res.ok else f"failed:{res.stage}"))
            if res.ok:                                 # verified: build + vet + the agent's repro
                verified_patch = patch_now
                internal_ok = True
                break
            feedback = f"[{res.stage}] {res.detail}"

    # Phase 7: finalize. Submit ONLY a fix the agent verified (build + vet + repro). Otherwise
    # abstain -- we never ship a fix we could not confirm, even if it merely compiles. If the
    # model cannot produce a verifiable fix, that is a model limitation to document, not to paper
    # over by submitting unverified patches.
    if verified_patch:
        code_patch, status = verified_patch, "resolved_internally"
    elif submit_unvalidated and last_applied_patch.strip():
        code_patch, status = last_applied_patch, "gave_up"
    else:
        code_patch = ""
        status = "abstained" if ever_applied else "no_edits"

    title, body = finalize.pr_text(llm, problem_statement, code_patch) if code_patch.strip() else ("", "")
    say(f"finalize: status={status} patch_bytes={len(code_patch)}")
    tracer.decision("finalize", status=status, patch_bytes=len(code_patch), attempts=attempts)

    return AgentResult(status=status, code_patch=code_patch, repro_code=repro_code,
                       pr_title=title, pr_body=body, attempts=attempts,
                       internal_ok=internal_ok,
                       attempt_patch=(code_patch or last_applied_patch), log=log,
                       trace=tracer.to_dict())
