"""Typed contracts shared across stages. Stage 0 only needs CommandResult."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def tail(self, n: int = 600) -> str:
        blob = (self.stdout + "\n" + self.stderr).strip()
        return blob[-n:]
