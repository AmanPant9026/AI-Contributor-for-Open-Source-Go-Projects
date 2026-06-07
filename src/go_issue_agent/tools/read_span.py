"""read_span -- read just lines N..M of a file.

The agent's context window is small and precious, so it reads the function it
cares about, not the whole 9,000-line file. Line numbers are 1-indexed and
inclusive, clamped to the file's real bounds so out-of-range requests never crash.
"""
from __future__ import annotations

from pathlib import Path

from .fileio import _safe


def read_span(root: str | Path, rel: str, start: int, end: int,
              *, with_line_numbers: bool = True) -> str:
    """Return lines [start, end] (1-indexed, inclusive) of `rel`.

    `start`/`end` are clamped into range; if start > end nothing is returned.
    """
    text = _safe(root, rel).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    n = len(lines)
    start = max(1, start)
    end = min(n, end)
    if start > end:
        return ""
    chosen = lines[start - 1:end]
    if not with_line_numbers:
        return "\n".join(chosen)
    width = len(str(end))
    return "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(chosen, start=start))
