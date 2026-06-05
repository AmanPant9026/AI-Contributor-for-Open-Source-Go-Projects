"""Run a command inside the pinned Docker sandbox against a mounted workspace.

The host directory is mounted read/write at /workspace; the command runs there.
Nothing executes on the host, so untrusted / model-generated changes are contained
to a throwaway container that disappears on exit (--rm)."""
from __future__ import annotations
import subprocess
import time
from pathlib import Path

from ..config import settings
from ..models import CommandResult


def run_in_sandbox(workspace: str | Path, command: str, *, image: str | None = None,
                   timeout_s: int = 900, network: bool = True) -> CommandResult:
    image = image or settings.sandbox_image
    ws = str(Path(workspace).resolve())
    docker_cmd = ["docker", "run", "--rm", "-v", f"{ws}:/workspace", "-w", "/workspace"]
    if not network:
        docker_cmd += ["--network", "none"]
    docker_cmd += [image, "bash", "-c", command]

    start = time.time()
    try:
        proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout_s)
        return CommandResult(command, proc.returncode, proc.stdout, proc.stderr, time.time() - start)
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        err = (e.stderr if isinstance(e.stderr, str) else "") + f"\n[timeout after {timeout_s}s]"
        return CommandResult(command, 124, out, err, time.time() - start)
    except FileNotFoundError:
        return CommandResult(command, 127, "", "docker not found on PATH", time.time() - start)
