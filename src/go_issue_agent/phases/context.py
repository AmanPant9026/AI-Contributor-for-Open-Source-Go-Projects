"""Phase 3 -- gather context: exact source first, then a BOUNDED agentic loop.

The decisive fix after the first gate-4 run: we no longer rely on the model to
find the right lines. `focus_snippets` deterministically pulls the actual
functions (with their exact repo-relative paths) from the files that mention the
issue terms -- by symbol-name match and by search-hit -- so the model can copy a
real SEARCH block verbatim. The model may still READ/SEARCH for more, but the
critical source is always present. The loop is bounded and de-duplicated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..indexing import ast_nav
from ..llm.client import LLMClient
from ..tools import read_span, search_code
from .localize import Localization
from .prompts import render

_READ_RE = re.compile(r"^\s*READ\s+(\S+)\s+(\d+)\s+(\d+)", re.I)
_SEARCH_RE = re.compile(r"^\s*SEARCH\s+(.+)$", re.I)
_DONE_RE = re.compile(r"^\s*DONE\b", re.I)


@dataclass
class Context:
    text: str                 # assembled raw code/snippets the model has seen
    actions: list[str]        # the actions it took (for logging/tests)
    primary_file: str | None = None   # the file the model should edit (from focus)


def _ignored(path: str) -> bool:
    return any(p.startswith((".", "_")) or p == "testdata" for p in Path(path).parts)


def focus_snippets(repo_dir: str | Path, terms: list[str], candidates: list[str],
                   *, max_funcs: int = 6) -> str:
    """The exact functions worth editing, with their exact paths.

    Files are ranked so the one that DEFINES a symbol whose name matches an issue
    term wins (definition beats mere references), then by localize-candidate order,
    then by raw term-hit count. A symbol is included if its name matches a term or
    it encloses a term-hit line. Searches the whole library so a file surfaces even
    if it wasn't a top candidate.
    """
    repo_dir = Path(repo_dir)
    long_terms = [t for t in terms if len(t) >= 4]

    hits: dict[str, list[int]] = {}
    for t in terms:
        for h in search_code.search_code(repo_dir, re.escape(t)):
            if h.path.endswith("_test.go") or _ignored(h.path):
                continue
            hits.setdefault(h.path, []).append(h.line)

    cand_index = {p: i for i, p in enumerate(candidates)}
    files = set(hits) | {p for p in candidates if not p.endswith("_test.go") and not _ignored(p)}

    # parse once; find symbols whose NAME matches a term (i.e. definitions)
    info: dict[str, tuple[list, list]] = {}
    for path in files:
        try:
            syms = ast_nav.parse_file(repo_dir / path)
        except Exception:  # noqa: BLE001
            syms = []
        name_syms = [s for s in syms
                     if any(t.lower() in s.name.lower() or s.name.lower() in t.lower()
                            for t in long_terms)]
        info[path] = (syms, name_syms)

    def score(path: str) -> tuple:
        syms, name_syms = info[path]
        is_cand = path in cand_index
        return (bool(name_syms), is_cand,
                -cand_index.get(path, 9999) if is_cand else -9999,
                len(hits.get(path, [])))

    ordered = sorted(files, key=score, reverse=True)

    blocks: list[str] = []
    picked: set[tuple[str, str]] = set()
    for path in ordered:
        if len(blocks) >= max_funcs:
            break
        syms, name_syms = info[path]
        chosen = list(name_syms)                          # (a) definitions matching a term
        for ln in sorted(set(hits.get(path, []))):        # (b) symbols enclosing a term-hit
            s = next((s for s in syms if s.start_line <= ln <= s.end_line), None)
            if s is not None:
                chosen.append(s)
        for s in chosen:
            key = (path, s.name)
            if key in picked:
                continue
            picked.add(key)
            raw = read_span.read_span(repo_dir, path, s.start_line, s.end_line,
                                      with_line_numbers=False)
            blocks.append(f"// FILE: {path}  (copy SEARCH text verbatim from here)\n{raw}")
            if len(blocks) >= max_funcs:
                break
    return "\n\n".join(blocks)


def gather(llm: LLMClient, repo_dir: str | Path, loc: Localization,
           problem_statement: str, *, max_reads: int = 5) -> Context:
    repo_dir = Path(repo_dir)

    # 1) deterministic exact source (the critical part).
    focus = focus_snippets(repo_dir, loc.terms, loc.candidates)
    seen_blocks: list[str] = [focus] if focus else []
    actions: list[str] = []
    m_primary = re.search(r"// FILE: (\S+)", focus) if focus else None
    primary_file = m_primary.group(1) if m_primary else (loc.candidates[0] if loc.candidates else None)

    # 2) optional bounded, de-duplicated model-driven reads.
    done_actions: set[str] = set()
    for _ in range(max_reads):
        prompt = render(
            "context",
            problem_statement=problem_statement,
            repo_map=loc.repo_map_skeleton,
            candidates="\n".join(loc.candidates) or "(none)",
            seen="\n\n".join(seen_blocks) or "(nothing yet)",
        )
        action = llm.complete([{"role": "user", "content": prompt}], max_tokens=64).strip()
        first_line = action.splitlines()[0] if action else ""
        actions.append(first_line)

        if _DONE_RE.search(first_line):
            break
        if first_line in done_actions:        # model repeating itself -> stop wasting budget
            break
        done_actions.add(first_line)

        m = _READ_RE.match(first_line)
        if m:
            path, s, e = m.group(1), int(m.group(2)), int(m.group(3))
            code = read_span.read_span(repo_dir, path, s, e, with_line_numbers=False)
            if code:
                seen_blocks.append(f"// FILE {path} (lines {s}-{e})\n{code}")
            continue
        m = _SEARCH_RE.match(first_line)
        if m:
            ghits = search_code.search_code(repo_dir, m.group(1).strip())
            joined = "\n".join(f"{h.path}:{h.line}: {h.text}" for h in ghits[:20]) or "(no hits)"
            seen_blocks.append(f"// SEARCH {m.group(1).strip()}\n{joined}")
            continue
        break  # unparseable

    if not seen_blocks and loc.candidates:
        top = loc.candidates[0]
        code = read_span.read_span(repo_dir, top, 1, 80, with_line_numbers=False)
        seen_blocks.append(f"// FILE {top} (lines 1-80)\n{code}")

    return Context(text="\n\n".join(seen_blocks), actions=actions, primary_file=primary_file)
