"""Host-side git operations: keep a local clone cache and check out a ref.

Later stages will switch to per-run git worktrees for isolation; Stage 0 keeps a
single working checkout, which is enough to prove the sandbox can build/test it."""
from __future__ import annotations
import subprocess
from pathlib import Path

CACHE_DIR = Path(".cache/repos")


def _git(args: list[str], cwd: str | Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def ensure_clone(clone_url: str, name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / name
    if (dest / ".git").exists():
        _git(["fetch", "--all", "--tags", "--quiet"], cwd=dest)
    else:
        _git(["clone", "--quiet", clone_url, str(dest)])
    return dest


def checkout(repo_dir: str | Path, ref: str) -> None:
    _git(["-C", str(repo_dir), "checkout", "--force", "--quiet", ref])
    _git(["-C", str(repo_dir), "reset", "--hard", "--quiet", ref])
    _git(["-C", str(repo_dir), "clean", "-fdq"])


def current_commit(repo_dir: str | Path) -> str:
    return _git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()
