"""edits -- parse and apply the model's search/replace blocks.

After two gate-4 runs the lesson is firm: a small model cannot be trusted to
write a correct file path, and any path placeholder we show it gets echoed
verbatim. So the agent tells the model WHICH file to edit and the model outputs
ONLY the blocks (no filename); we apply them to that known `default_target`. A
filename line is still TOLERATED (and cleaned) if the model adds one anyway.

Block format (filename optional; ``` fences and stray labels tolerated):

    <<<<<<< SEARCH
    <exact current lines>
    =======
    <replacement lines>
    >>>>>>> REPLACE

Everything here is pure and deterministic, unit-tested without a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tools.fileio import _safe

# The core block (filename, if any, lives on the preceding line).
_CORE_RE = re.compile(
    r"<<<<<<< SEARCH\n(?P<search>.*?)\n?"
    r"=======\n(?P<replace>.*?)\n?"
    r">>>>>>> REPLACE",
    re.DOTALL,
)
# A preceding line that plausibly names a file (so we only treat real path-ish
# lines as paths, not arbitrary prose).
_PATHISH = re.compile(r"(\.go\b|^\s*FILE(NAME)?\b|\b(file|filename|path|edit)\s*[:=])", re.I)
_PLACEHOLDERS = {"FILENAME", "FILE", "PATH", "RELATIVE/PATH/TO/FILE.GO", ""}


@dataclass(frozen=True)
class Edit:
    path: str            # repo-relative file ("" => use the apply-time default_target)
    search: str          # exact text to find ("" => create file)
    replace: str         # text to put in its place


@dataclass(frozen=True)
class ApplyEditsResult:
    applied: int
    failures: list[str]
    changed_files: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures


def _clean_path(raw: str) -> str:
    p = raw.strip().strip("`").strip()
    p = re.sub(r"^```[a-zA-Z0-9]*\s*", "", p).strip()
    p = re.sub(r"^(//|\*|-|#)\s*", "", p).strip()                 # comment / bullet
    p = re.sub(r"^(filename|file|path|edit)\s*[:=]?\s*", "", p, flags=re.I).strip()
    p = p.strip("`'\"<> ").strip()
    return "" if p.upper() in _PLACEHOLDERS else p


def parse_edits(text: str) -> list[Edit]:
    """Extract all search/replace blocks (path optional)."""
    edits: list[Edit] = []
    for m in _CORE_RE.finditer(text):
        path = ""
        pre = text[:m.start()].splitlines()
        for line in reversed(pre):                                # nearest non-empty line
            if line.strip() == "":
                continue
            cand = line.strip()
            if "<<<<<<<" not in cand and len(cand) <= 120 and _PATHISH.search(cand):
                path = _clean_path(cand)
            break
        edits.append(Edit(path=path, search=m.group("search"), replace=m.group("replace")))
    return edits


def _resolve(repo_dir: Path, rel: str) -> Path | None:
    """Resolve a path to a real file, tolerating a mangled prefix via a UNIQUE
    basename match among real (non-test, non-ignored) files."""
    try:
        direct = _safe(repo_dir, rel)
    except ValueError:
        direct = None
    if direct is not None and direct.exists():
        return direct
    name = Path(rel).name
    matches = []
    for q in Path(repo_dir).rglob(name):
        if not q.is_file():
            continue
        if any(p.startswith((".", "_")) or p == "testdata"
               for p in q.relative_to(repo_dir).parts):
            continue
        matches.append(q)
    return matches[0] if len(matches) == 1 else None


def apply_edits(repo_dir: str | Path, edits: list[Edit],
                default_target: str | None = None) -> ApplyEditsResult:
    """Apply edits. A block with no path uses `default_target` (the file the agent
    told the model to edit). First exact match of SEARCH is replaced. Failures are
    recorded (not raised) so the caller can feed them back to the model."""
    repo_dir = Path(repo_dir)
    applied = 0
    failures: list[str] = []
    changed: list[str] = []

    for e in edits:
        eff = e.path or (default_target or "")
        if not eff:
            failures.append("no target file (output blocks for the file you were told to edit)")
            continue

        if e.search.strip() == "":                                # create / overwrite
            try:
                target = _safe(repo_dir, eff)
            except ValueError as ex:
                failures.append(f"{eff}: {ex}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(e.replace, encoding="utf-8")
            applied += 1
            changed.append(eff)
            continue

        target = _resolve(repo_dir, eff)
        if target is None:
            failures.append(f"{eff}: file not found")
            continue
        rel = str(target.relative_to(repo_dir.resolve())) if target.is_absolute() else str(target)
        content = target.read_text(encoding="utf-8", errors="replace")
        if e.search not in content:
            failures.append(f"{rel}: SEARCH text not found (copy it verbatim, including tabs)")
            continue
        new_content = content.replace(e.search, e.replace, 1)
        if new_content == content:
            failures.append(f"{rel}: SEARCH and REPLACE are identical -- no change made; make a real edit")
            continue
        target.write_text(new_content, encoding="utf-8")
        applied += 1
        if rel not in changed:
            changed.append(rel)

    return ApplyEditsResult(applied=applied, failures=failures, changed_files=changed)
