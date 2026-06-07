"""Phase 7 -- finalize: extract the code-only patch and write a PR title/body.

The agent's reproduction test is a scratch file used only for self-checking; the
SUBMITTED patch is code-only (tests excluded), mirroring the gold fix.patch. This
keeps localization precision honest.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..llm.client import LLMClient
from .prompts import render

_TITLE_RE = re.compile(r"TITLE:\s*(.+)", re.I)


def code_only_diff(repo_dir: str | Path) -> str:
    """git diff of the working tree vs HEAD, EXCLUDING *_test.go (and untracked
    scratch tests, which don't appear in `git diff` anyway)."""
    repo_dir = str(Path(repo_dir).resolve())
    out = subprocess.run(
        ["git", "-C", repo_dir, "diff", "--", ".", ":(exclude)*_test.go"],
        capture_output=True, text=True,
    )
    return out.stdout


def pr_text(llm: LLMClient, problem_statement: str, diff: str) -> tuple[str, str]:
    if not diff.strip():
        return ("Fix issue", "")
    out = llm.complete([{"role": "user", "content": render(
        "pr", problem_statement=problem_statement, diff=diff[:4000])}], max_tokens=300)
    title, body = "Fix issue", out.strip()
    m = _TITLE_RE.search(out)
    if m:
        title = m.group(1).strip()
        body = out[m.end():].lstrip()
        body = re.sub(r"^BODY:\s*", "", body, flags=re.I).strip()
    return title, body
