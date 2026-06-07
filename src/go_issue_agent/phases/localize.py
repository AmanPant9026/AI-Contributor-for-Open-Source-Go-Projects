"""Phase 2 -- localize: turn the issue text into a ranked list of suspect files.

Deterministic (no LLM): pull candidate identifiers out of the bug report, use
them to (a) bias repo_map's PageRank toward issue-relevant files and (b) seed
exact searches. The output orients the model before it reads anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import edits  # noqa: F401  (keeps package import order sane)
from ..indexing import repo_map
from ..tools import search_code

# Words too common to be useful localization signal.
_STOP = {
    "the", "and", "for", "that", "this", "with", "when", "should", "return",
    "returns", "value", "valid", "invalid", "error", "errors", "test", "func",
    "string", "bool", "int", "code", "bug", "issue", "fails", "fail", "pass",
    "expected", "actual", "result", "go", "golang", "validator", "validate",
    "field", "struct", "type", "func", "nil", "true", "false", "package",
    "playground", "github", "com", "www", "https", "http", "been", "you", "your",
    "starting", "start", "have", "has", "are", "was", "not", "but", "use", "using",
    "v10", "v9", "version", "library", "function", "method", "example", "want",
}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
@dataclass
class Localization:
    terms: list[str]                       # issue-derived search terms
    candidates: list[str] = field(default_factory=list)   # repo-relative suspect files, best first
    repo_map_skeleton: str = ""            # the (focused) repo map text


def extract_issue_terms(text: str) -> list[str]:
    """Identifier-like tokens worth searching for, most-specific first."""
    raw: list[str] = []
    for m in _BACKTICK_RE.finditer(text):          # `code` spans first (highest signal)
        raw += _TOKEN_RE.findall(m.group(1))
    raw += _TOKEN_RE.findall(text)
    seen: dict[str, int] = {}
    for tok in raw:
        if tok.lower() in _STOP or len(tok) < 3:
            continue
        if re.fullmatch(r"0?x[0-9a-fA-F]{2,}", tok):   # hex frame offsets/addresses (x1ac, 0x..)
            continue
        if re.fullmatch(r"func\d+", tok):              # anonymous stack frames (func1, func2)
            continue
        if re.fullmatch(r"\d{5,}", tok):               # long bare numbers
            continue
        seen[tok] = seen.get(tok, 0) + 1
    # CamelCase / digit-bearing tokens are likely real identifiers/codes -> rank up.
    def score(t: str) -> tuple:
        has_digit = any(c.isdigit() for c in t)
        mixed = any(c.isupper() for c in t[1:])
        return (has_digit, mixed, seen[t], len(t))
    return sorted(seen, key=score, reverse=True)[:14]


def localize(repo_dir: str | Path, problem_statement: str, *, top_k: int = 8) -> Localization:
    repo_dir = Path(repo_dir)
    terms = extract_issue_terms(problem_statement)

    # Files that literally mention the issue terms (weighted by hit count).
    hit_counts: dict[str, int] = {}
    for t in terms:
        for h in search_code.search_code(repo_dir, re.escape(t)):
            hit_counts[h.path] = hit_counts.get(h.path, 0) + 1

    # Bias PageRank's random-jump toward those files (the Stage-3 personalization hook).
    personalization = hit_counts or None
    rm = repo_map.build_repo_map(repo_dir, personalization=personalization)

    rank_pos = {fi.path: i for i, fi in enumerate(rm.ranked_files)}
    # Candidates: term-mentioning files first (by hits, then rank), then fill from the map.
    mentioned = sorted(hit_counts, key=lambda p: (-hit_counts[p], rank_pos.get(p, 1e9)))
    candidates = list(dict.fromkeys(mentioned + [fi.path for fi in rm.ranked_files]))[:top_k]

    return Localization(terms=terms, candidates=candidates, repo_map_skeleton=rm.skeleton)
