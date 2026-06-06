"""Run a command inside the pinned Docker sandbox against a mounted workspace.

The host directory is mounted read/write at /workspace; the command runs there.
Nothing executes on the host, so untrusted / model-generated changes are contained
to a throwaway container that disappears on exit (--rm).

We use `bash -c` (NOT `-lc`): a login shell re-reads profile files and can reset
PATH, dropping /usr/local/go/bin so `go` isn't found. A non-login shell keeps the
image's environment intact. We also prepend the Go bin dir defensively.
"""
from __future__ import annotations
import subprocess
import time
from pathlib import Path

from ..config import settings
from ..models import CommandResult

# golang image keeps go here; prepend so it's always on PATH regardless of shell init
_GO_PATH_PREFIX = 'export PATH="/usr/local/go/bin:${GOPATH:-/go}/bin:$PATH"; '


def run_in_sandbox(workspace: str | Path, command: str, *, image: str | None = None,
                   timeout_s: int = 900, network: bool = True,
                   extra_mounts: list[tuple[str, str]] | None = None) -> CommandResult:
    image = image or settings.sandbox_image
    ws = str(Path(workspace).resolve())
    docker_cmd = ["docker", "run", "--rm", "-v", f"{ws}:/workspace", "-w", "/workspace"]
    for host_dir, cont_dir in (extra_mounts or []):
        Path(host_dir).mkdir(parents=True, exist_ok=True)
        docker_cmd += ["-v", f"{Path(host_dir).resolve()}:{cont_dir}"]
    if not network:
        docker_cmd += ["--network", "none"]
    docker_cmd += [image, "bash", "-c", _GO_PATH_PREFIX + command]

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
