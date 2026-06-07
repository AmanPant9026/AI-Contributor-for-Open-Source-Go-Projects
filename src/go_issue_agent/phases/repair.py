"""Phase 4 -- the model writes a reproduction test and proposes a code fix.

Both calls are pure given an LLMClient, so they're testable with a fake model.
The repro test is the agent's own self-check signal (Stage-4 plan, Q2); the fix
is expressed as search/replace blocks (Q1), parsed by edits.parse_edits.
"""
from __future__ import annotations

import re

from ..edits import Edit, parse_edits
from ..llm.client import LLMClient
from .prompts import render

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n|\n```$", re.M)
_TESTFUNC_RE = re.compile(r"func\s+(Test[A-Za-z0-9_]+)\s*\(")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def propose_repro(llm: LLMClient, problem_statement: str, context: str,
                  feedback: str = "") -> str:
    """Ask the model for a Go test that fails on the bug and passes once fixed."""
    fb = f"\nIMPORTANT -- your previous test was rejected:\n{feedback}\n" if feedback else ""
    out = llm.complete([{"role": "user", "content": render(
        "repro", problem_statement=problem_statement, context=context, feedback=fb)}],
        max_tokens=900)
    return _strip_fences(out)


def repro_test_name(repro_code: str) -> str:
    m = _TESTFUNC_RE.search(repro_code)
    return m.group(1) if m else "TestAgentRepro"


def propose_fix(llm: LLMClient, problem_statement: str, context: str,
                feedback: str = "", target_file: str | None = None) -> list[Edit]:
    """Ask the model for a minimal fix as search/replace blocks; parse them.
    The model is told which file to edit and outputs path-free blocks."""
    fb = f"\nPREVIOUS ATTEMPT FAILED:\n{feedback}\nTry a different fix.\n" if feedback else ""
    out = llm.complete([{"role": "user", "content": render(
        "fix", problem_statement=problem_statement, context=context, feedback=fb,
        target=target_file or "the file shown in SOURCE")}], max_tokens=900)
    return parse_edits(out)
