"""Repo registry -- the harness is no longer hard-wired to validator.

Each instance's id is prefixed with a repo key (`validator-1284`, `cobra-2095`, `gin-3912`);
`repo_for_instance` maps an id back to where its code lives and which Go image builds it. This
is the one place that knows repo-specific facts, so adding an approved repo is a registry
entry, not a code change. All repos here are from the assignment's approved list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS_CACHE = ROOT / ".cache" / "repos"


@dataclass(frozen=True)
class Repo:
    key: str            # task-id prefix, e.g. "validator", "cobra"
    owner: str          # GitHub owner
    name: str           # GitHub repo name
    go_image: str       # Docker image that builds this repo
    held_out: bool      # True = test set we do NOT tune against (e.g. cobra, gin)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def clone_dir(self) -> Path:
        return REPOS_CACHE / self.key


# Approved repos only (assignment: gin / cobra / validator / golangci-lint). validator is the
# repo we develop against; the others are held-out checks of the same agent.
REGISTRY: dict[str, Repo] = {
    "validator": Repo("validator", "go-playground", "validator", "golang:1.24", held_out=False),
    "cobra":     Repo("cobra",     "spf13",         "cobra",     "golang:1.24", held_out=True),
    "gin":       Repo("gin",       "gin-gonic",     "gin",       "golang:1.24", held_out=True),
}


def repo_for_instance(instance_id: str) -> Repo:
    """Map `validator-1284` -> the validator Repo, by id prefix."""
    key = instance_id.split("-", 1)[0]
    if key not in REGISTRY:
        raise KeyError(f"no repo registered for instance id {instance_id!r} (prefix {key!r})")
    return REGISTRY[key]


def resolve_clone(instance_dir_name: str, repo_field: str | None = None) -> tuple[Path, str]:
    """Where an instance's repo should be cloned, and from which URL -- the registry entry for
    the instance's repo prefix. (`repo_field` is accepted for call-site compatibility.)"""
    repo = repo_for_instance(instance_dir_name)
    return repo.clone_dir, repo.clone_url
