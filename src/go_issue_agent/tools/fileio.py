"""fileio -- the agent's basic file access.

Deliberately boring and safe: all paths are resolved relative to a fixed `root`
and may not escape it (no `../` traversal out of the repo). Everything returns
plain Python types so it is trivial to unit-test.
"""
from __future__ import annotations

from pathlib import Path


def _safe(root: str | Path, rel: str) -> Path:
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"path escapes root: {rel!r}")
    return target


def list_files(root: str | Path, *, suffix: str | None = ".go",
               include_tests: bool = True) -> list[str]:
    """Repo-relative paths of files under `root`, optionally filtered by suffix.

    Results are sorted for determinism. Hidden dirs and the module cache are skipped.
    """
    root = Path(root).resolve()
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if suffix and p.suffix != suffix:
            continue
        if not include_tests and p.name.endswith("_test.go"):
            continue
        out.append(str(p.relative_to(root)))
    return out


def read_file(root: str | Path, rel: str) -> str:
    """Read an entire file (UTF-8). Prefer read_span for large files."""
    return _safe(root, rel).read_text(encoding="utf-8", errors="replace")


def write_file(root: str | Path, rel: str, content: str) -> None:
    """Write a file, creating parent directories as needed."""
    target = _safe(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
