"""Phase 4c -- execution-evidence localization.

After the agent's reproduction is confirmed to FAIL on the unpatched base, we RUN it
(under coverage) and mine the run for where the bug lives. The evidence ladder, best
available rung first:

    compiler error file:line   (10)   -- a build error names the file directly
    panic / runtime trace      (9-8)  -- the deepest *repo* frame in a stack trace
    coverage of the repro      (~8)   -- the files the repro actually executed
    [then the existing lexical + PageRank ranking ORDERS whatever survives]

The top non-empty rung wins; we fall through when a rung is blank. Coverage is the
rung that fires for SILENT bugs (no crash, no compile error) -- which is most of them,
and is what prose keyword-matching could not localize. Everything here is pure (string
in, ranking out) so it is fully unit-tested without Docker; the agent supplies the
captured run output and coverage profile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# `module/path/file.go:12.34,15.2 4 1`  -> (file, hitcount)
_COVER_LINE = re.compile(r"^(\S+\.go):\d+\.\d+,\d+\.\d+\s+\d+\s+(\d+)$")
# `./baked_in.go:123:4: undefined: x`   -> file named by a compiler error
_COMPILE_ERR = re.compile(r"(?:^|\s)\.?/?([\w./-]+\.go):\d+:\d+:")
# `\tgithub.com/x/y/baked_in.go:512 +0x1a`  -> a stack-trace frame
_TRACE_FRAME = re.compile(r"\t(\S+\.go):\d+(?:\s|$)")


@dataclass
class Evidence:
    covered: list[str] = field(default_factory=list)       # files the repro executed (rung ~8)
    traced: list[str] = field(default_factory=list)        # files in a panic/stack trace (rung 9-8)
    compiled_err: list[str] = field(default_factory=list)  # files named by a compiler error (rung 10)

    def any(self) -> bool:
        return bool(self.covered or self.traced or self.compiled_err)


def module_path(repo_dir: str | Path) -> str:
    """The Go module path from go.mod, used to strip the prefix off coverage paths."""
    try:
        for line in (Path(repo_dir) / "go.mod").read_text(errors="replace").splitlines():
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except OSError:
        pass
    return ""


def _strip(path: str, module: str) -> str:
    prefix = module + "/" if module else ""
    return path[len(prefix):] if prefix and path.startswith(prefix) else path


def parse_coverage(profile_text: str, module: str = "") -> list[str]:
    """Repo-relative source files with EXECUTED statements (count > 0), tests excluded."""
    totals: dict[str, int] = {}
    for line in profile_text.splitlines():
        m = _COVER_LINE.match(line)
        if not m:
            continue
        f = _strip(m.group(1), module)
        totals[f] = totals.get(f, 0) + int(m.group(2))
    return [f for f, c in totals.items() if c > 0 and not f.endswith("_test.go")]


def parse_compiler_files(output: str) -> list[str]:
    """Files named by Go compiler errors (e.g. `./foo.go:12:3: ...`), tests excluded."""
    out = []
    for m in _COMPILE_ERR.finditer(output):
        f = m.group(1).lstrip("./")
        if f.endswith(".go") and not f.endswith("_test.go"):
            out.append(f)
    return list(dict.fromkeys(out))


def parse_trace_files(output: str, known_files: list[str]) -> list[str]:
    """Repo frames from a panic / goroutine stack trace, deepest first. Only files that
    exist in the repo (drops stdlib `/usr/local/go/...` frames); tests excluded."""
    known = set(known_files)
    by_base: dict[str, str] = {}
    for k in known:
        by_base.setdefault(Path(k).name, k)
    out = []
    for m in _TRACE_FRAME.finditer(output):
        raw = m.group(1)
        cand = raw if raw in known else by_base.get(Path(raw).name)
        if cand and not cand.endswith("_test.go"):
            out.append(cand)
    return list(dict.fromkeys(out))


def rerank(loc_candidates: list[str], ev: Evidence) -> list[str]:
    """Apply the ladder. Compiler-named files first, then trace-named, then the COVERED
    files ordered by their existing lexical/PageRank position, then any covered file the
    ranking didn't know about, then the full prose ranking as the fallback tail. The old
    lexical+PageRank ranking is never discarded -- it does the ordering and the fallback."""
    ranked: list[str] = []

    def add(items):
        for x in items:
            if x and x not in ranked:
                ranked.append(x)

    covered = set(ev.covered)
    loc_set = set(loc_candidates)
    add(ev.compiled_err)                                           # rung 10
    add(ev.traced)                                                 # rung 9-8
    add([c for c in loc_candidates if c in covered])               # rung ~8, ordered by lexical/PR
    add([f for f in ev.covered if f not in loc_set])               # covered but unranked by prose
    add(loc_candidates)                                            # rungs 7 + 4: prose fallback
    return ranked
