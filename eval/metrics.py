"""Pure scoring functions for the eval harness.

No Docker, no git, no file I/O here -- just functions that turn raw facts
(which files a diff touches, which tests passed) into the numbers that mirror
the assignment's grading axes. Keeping these pure makes them unit-testable
without a sandbox.

A result is more than pass/fail. A single boolean conflates two very different
situations -- "a patch ran but was wrong" versus "no patch ran at all" -- so
each result carries a STATUS, and each gate is TRI-STATE (ok / fail / n/a):

  status        meaning                                   gates       localization  resolved
  ------------  ----------------------------------------  ----------  ------------  --------
  resolved      applied; the failing test now passes      real        real          True
  unresolved    applied; test still fails (wrong code)    real        real          False
  noop          empty candidate; nothing executed         n/a         n/a           False
  apply_failed  non-empty diff that would not apply        n/a         real (intent) False

Metric -> grader axis:
  resolved              -> "produces relevant code changes" (headline, = pass@1)
  localization recall   -> "identifies the right files"
  localization precision-> (did we ALSO touch files we shouldn't have)
  build / vet / fmt     -> "follows conventions" + "runs validation" (diagnostics)
  diff_similarity       -> hint toward "relevant changes" (SECONDARY, never optimized for)
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# status constants
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
NOOP = "noop"
APPLY_FAILED = "apply_failed"

# `+++ b/path` is the post-image filename in a unified diff.
_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def files_in_diff(diff_text: str) -> set[str]:
    """Return the set of repo-relative file paths a unified diff touches."""
    files: set[str] = set()
    text = diff_text or ""
    for m in _PLUS_RE.finditer(text):
        p = m.group(1).strip()
        if p and p != "/dev/null":
            files.add(p)
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b = parts[3]
                b = b[2:] if b.startswith("b/") else b
                if b and b != "/dev/null":
                    files.add(b)
    return files


def localization(cand_files: set[str], gold_files: set[str]) -> tuple[float, float]:
    """(recall, precision) of candidate file set vs the gold file set.

    recall    = correct files touched / gold files       (1.0 if gold is empty)
    precision = correct files touched / candidate files   (1.0 if candidate empty)

    NOTE: callers pass localization == (None, None) for a no-op candidate, where
    there was no localization *attempt* at all -- that is reported as n/a, not 0.
    """
    cand, gold = set(cand_files), set(gold_files)
    hits = len(cand & gold)
    recall = 1.0 if not gold else hits / len(gold)
    precision = 1.0 if not cand else hits / len(cand)
    return round(recall, 3), round(precision, 3)


def diff_similarity(cand_diff: str, gold_diff: str) -> float:
    """Rough similarity of the changed *content* of two diffs, in [0,1].
    Secondary signal only -- reported, never gated on."""
    def changed_lines(t: str) -> str:
        out = []
        for ln in (t or "").splitlines():
            if ln[:1] in "+-" and not ln.startswith(("+++", "---")):
                out.append(ln[1:].strip())
        return "\n".join(out)

    return round(difflib.SequenceMatcher(None, changed_lines(cand_diff),
                                         changed_lines(gold_diff)).ratio(), 3)


@dataclass
class InstanceScore:
    """The scored result of one candidate patch on one instance.

    Tri-state fields (build_ok / vet_ok / fmt_ok / recall / precision) use None
    to mean 'not applicable' (no candidate code to judge / no localization
    attempt) -- distinct from False / 0.0 which mean 'judged and failed'.
    """
    instance_id: str
    candidate: str                 # "gold" | "empty" | "agent" | ...
    status: str                    # RESOLVED | UNRESOLVED | NOOP | APPLY_FAILED
    resolved: bool                 # FAIL_TO_PASS and PASS_TO_PASS all pass
    ftp_passed: bool
    ptp_passed: bool
    recall: float | None
    precision: float | None
    build_ok: bool | None          # None = n/a
    vet_ok: bool | None
    fmt_ok: bool | None
    diff_similarity: float
    note: str = ""
    cand_files: list = field(default_factory=list)
    gold_files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def resolution_rate(scores: list[InstanceScore]) -> float:
    if not scores:
        return 0.0
    return round(sum(1 for s in scores if s.resolved) / len(scores), 3)


def status_counts(scores: list[InstanceScore]) -> dict:
    counts = {RESOLVED: 0, UNRESOLVED: 0, NOOP: 0, APPLY_FAILED: 0}
    for s in scores:
        counts[s.status] = counts.get(s.status, 0) + 1
    return counts


def _gate(x: bool | None) -> str:
    return "n/a" if x is None else ("ok" if x else "FAIL")


def _num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def format_table(scores: list[InstanceScore]) -> str:
    """A compact fixed-width results table for the terminal."""
    cols = ["instance", "cand", "status", "recall", "prec", "build", "vet", "fmt", "diff~"]
    rows = [cols]
    for s in scores:
        rows.append([
            s.instance_id.split("__")[-1],
            s.candidate,
            s.status,
            _num(s.recall),
            _num(s.precision),
            _gate(s.build_ok),
            _gate(s.vet_ok),
            _gate(s.fmt_ok),
            f"{s.diff_similarity:.2f}",
        ])
    widths = [max(len(r[i]) for r in rows) for i in range(len(cols))]
    out = []
    for ri, r in enumerate(rows):
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)))
        if ri == 0:
            out.append("  ".join("-" * widths[i] for i in range(len(cols))))
    return "\n".join(out)
