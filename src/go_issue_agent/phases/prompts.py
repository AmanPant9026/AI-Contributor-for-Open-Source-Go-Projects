"""Load the prompt templates from the repo-root prompts/ directory."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, **kw) -> str:
    """Render prompts/<name>.md with {placeholders}. Substituted values are
    inserted literally, so code containing braces is safe."""
    return _read(name).format(**kw)
