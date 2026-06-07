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
from .phases import finalize, localize, repair, validate as validate_phase
from .phases.validate import ValidationResult

# A validate_fn takes (repo_dir, repro_code) and returns a ValidationResult.
ValidateFn = Callable[[Path, str], ValidationResult]


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


def run_agent(problem_statement: str, repo_dir: str | Path, *, llm: LLMClient,
              base_ref: str | None = None, max_context_reads: int = 5,
              max_repair_attempts: int = 3, max_repro_attempts: int = 3,
              validate_fn: ValidateFn | None = None,
              reproduces_fn: Callable[[Path, str], bool] | None = None,
              run_existing_tests: bool = False, submit_unvalidated: bool = False,
              on_log: Callable[[str], None] | None = None) -> AgentResult:
    repo_dir = Path(repo_dir)
    base_ref = base_ref or repo_ops.current_commit(repo_dir)
    log: list[str] = []

    def say(msg: str) -> None:
        log.append(msg)
        if on_log:
            on_log(msg)

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

    # Phase 2: localize
    loc = localize.localize(repo_dir, problem_statement)
    say(f"localize: terms={loc.terms[:6]} candidates={loc.candidates[:5]}")

    # Phase 3: gather context (bounded loop)
    ctx = context_phase.gather(llm, repo_dir, loc, problem_statement,
                               max_reads=max_context_reads)
    say(f"context: actions={ctx.actions}")

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

    if not repro_valid:
        # We could not build a test that reproduces the bug, so we cannot VERIFY any fix.
        # Do no harm: abstain rather than submit something unverifiable.
        say("repro: could not produce a reproducing test -> abstaining (cannot verify a fix)")
        return AgentResult(status="no_repro", code_patch="", repro_code=repro_code,
                           attempts=0, internal_ok=False, log=log)

    # Phase 4b/5/6: propose -> apply -> validate, bounded repair loop
    feedback = ""
    internal_ok = False
    attempts = 0
    ever_applied = False
    verified_patch: str | None = None     # build + vet + repro ALL pass -> the only thing we submit
    last_applied_patch = ""                # most recent applied diff (for debug visibility only)
    for attempt in range(1, max_repair_attempts + 1):
        attempts = attempt
        repo_ops.checkout(repo_dir, base_ref)          # independent attempts
        edits = repair.propose_fix(llm, problem_statement, ctx.text, feedback,
                                   target_file=ctx.primary_file)
        if not edits:
            say(f"attempt {attempt}: model produced no edits")
            feedback = ("You produced no valid search/replace blocks. Output at least one "
                        "block using the <<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers.")
            continue
        from . import edits as edits_mod
        applied = edits_mod.apply_edits(repo_dir, edits, default_target=ctx.primary_file)
        if applied.applied == 0:                       # nothing changed -> retry with feedback
            say(f"attempt {attempt}: apply failed: {applied.failures}")
            feedback = "; ".join(applied.failures)
            continue
        ever_applied = True
        patch_now = finalize.code_only_diff(repo_dir)
        if patch_now.strip():
            last_applied_patch = patch_now
        res = validate_fn(repo_dir, repro_code)
        say(f"attempt {attempt}: edited {applied.changed_files} -> validate {res.stage} ok={res.ok}")
        if res.ok:                                     # verified: build + vet + the agent's repro
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

    return AgentResult(status=status, code_patch=code_patch, repro_code=repro_code,
                       pr_title=title, pr_body=body, attempts=attempts,
                       internal_ok=internal_ok,
                       attempt_patch=(code_patch or last_applied_patch), log=log)
