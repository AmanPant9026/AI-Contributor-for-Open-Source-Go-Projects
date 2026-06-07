"""go_tools -- run the Go toolchain inside the pinned Docker sandbox.

Thin wrappers over the Stage-0 sandbox runner, so the agent can compile, vet,
format-check, and test the repo without anything touching the host. The command
*strings* are built by pure helpers (`build_cmd`, `vet_cmd`, `test_cmd`,
`gofmt_check_cmd`) so they can be unit-tested without Docker; the run_* wrappers
just feed those strings to the sandbox.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..models import CommandResult
from ..sandbox.runner import run_in_sandbox

# Persistent Go module cache -> deps download once, not per run (see Stage 2).
_GOMOD_CACHE = Path(".cache/gomod")


# ----------------------------------------------------------- pure command builders

def build_cmd(pkgs: str = "./...") -> str:
    return f"go build {pkgs}"


def vet_cmd(pkgs: str = "./...") -> str:
    return f"go vet {pkgs}"


def test_cmd(test_names: list[str] | None = None, pkgs: str = "./...") -> str:
    if test_names:
        regex = "^(" + "|".join(test_names) + ")$"
        return f"go test -run '{regex}' {pkgs}"
    return f"go test {pkgs}"


def gofmt_check_cmd(go_files: list[str]) -> str:
    """gofmt -l on specific files; empty output (and exit 0) means all formatted.
    Scoped to the given files only -- never the whole tree (see Stage 2 §4.3)."""
    if not go_files:
        return "true"
    quoted = " ".join(f"'{f}'" for f in go_files)
    return f'test -z "$(gofmt -l {quoted} 2>/dev/null)"'


# ----------------------------------------------------------- sandboxed runners

def _run(repo_dir: str | Path, cmd: str, timeout_s: int = 1200) -> CommandResult:
    return run_in_sandbox(
        repo_dir, cmd, image=settings.sandbox_image, timeout_s=timeout_s,
        extra_mounts=[(str(_GOMOD_CACHE), "/go/pkg/mod")],
    )


def go_build(repo_dir: str | Path, pkgs: str = "./...") -> CommandResult:
    return _run(repo_dir, build_cmd(pkgs))


def go_vet(repo_dir: str | Path, pkgs: str = "./...") -> CommandResult:
    return _run(repo_dir, vet_cmd(pkgs))


def go_test(repo_dir: str | Path, test_names: list[str] | None = None,
            pkgs: str = "./...") -> CommandResult:
    return _run(repo_dir, test_cmd(test_names, pkgs))


def gofmt_check(repo_dir: str | Path, go_files: list[str]) -> CommandResult:
    return _run(repo_dir, gofmt_check_cmd(go_files))
