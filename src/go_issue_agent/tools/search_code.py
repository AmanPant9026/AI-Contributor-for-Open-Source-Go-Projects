"""search_code -- find a string/regex across the repo.

Thin wrapper over ripgrep (`rg`) when available -- it is fast and respects
.gitignore -- with a pure-Python fallback so the tool (and its tests) work even
where `rg` isn't installed. Returns structured hits, not raw text, so callers
don't parse strings.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Hit:
    path: str       # repo-relative
    line: int       # 1-indexed
    text: str       # the matching line (stripped of trailing newline)


def _ripgrep(root: Path, pattern: str, globs: list[str]) -> list[Hit] | None:
    rg = shutil.which("rg")
    if rg is None:
        return None
    cmd = [rg, "--line-number", "--no-heading", "--color", "never"]
    for g in globs:
        cmd += ["--glob", g]
    cmd += [pattern, "."]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if proc.returncode not in (0, 1):  # 1 = no matches; >1 = real error
        return None
    hits: list[Hit] = []
    for raw in proc.stdout.splitlines():
        # format: path:line:content
        parts = raw.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            hits.append(Hit(parts[0].lstrip("./"), int(parts[1]), parts[2]))
    return hits


def _python_fallback(root: Path, pattern: str, suffix: str) -> list[Hit]:
    rx = re.compile(pattern)
    hits: list[Hit] = []
    for p in sorted(root.rglob(f"*{suffix}")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(Hit(str(p.relative_to(root)), i, line))
    return hits


def search_code(root: str | Path, pattern: str, *, suffix: str = ".go",
                max_results: int = 200) -> list[Hit]:
    """Search the repo for `pattern` (a regex). Returns up to `max_results` hits,
    sorted by (path, line) for determinism."""
    root = Path(root).resolve()
    hits = _ripgrep(root, pattern, [f"*{suffix}"])
    if hits is None:
        hits = _python_fallback(root, pattern, suffix)
    hits = sorted(hits, key=lambda h: (h.path, h.line))
    return hits[:max_results]
