"""apply_patch -- apply a unified diff to a checkout, tolerantly.

Mirrors the proven apply logic from verify_gt.sh / run_eval.py: try `git apply`
(recount + ignore-whitespace) first, fall back to the classic `patch --fuzz=3`.
An empty diff is a successful no-op (the 'empty' candidate). Returns a small
result object so the caller knows whether it applied and why not.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApplyResult:
    applied: bool       # did the tree change?
    empty: bool         # was the diff empty (a legitimate no-op)?
    method: str         # "git" | "patch" | "none" | "failed"
    detail: str = ""    # stderr on failure, for diagnostics


def apply_patch(repo_dir: str | Path, diff_text: str) -> ApplyResult:
    repo_dir = str(Path(repo_dir).resolve())
    if not diff_text.strip():
        return ApplyResult(applied=False, empty=True, method="none")

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as tf:
        tf.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
        pf = tf.name
    try:
        g = subprocess.run(["git", "-C", repo_dir, "apply", "--recount",
                            "--ignore-whitespace", pf], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if g.returncode == 0:
            return ApplyResult(applied=True, empty=False, method="git")
        # Fallback to classic `patch`. CRITICAL: it must never block on a prompt.
        # An already-applied/reversed patch makes `patch` ask "Assume -R? [n]" and
        # wait on stdin; -N (forward, skip reversed/applied) + DEVNULL stdin make it
        # fail cleanly instead. -r /dev/null discards reject files so the tree stays clean.
        p = subprocess.run(["patch", "-d", repo_dir, "-p1", "--fuzz=3", "-N",
                            "-r", "/dev/null", "-i", pf], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if p.returncode == 0:
            return ApplyResult(applied=True, empty=False, method="patch")
        return ApplyResult(applied=False, empty=False, method="failed",
                           detail=(g.stderr + "\n" + p.stderr).strip())
    finally:
        Path(pf).unlink(missing_ok=True)
