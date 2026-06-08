"""Content-addressed cache of model completions.

The key is sha256(model + max_tokens + messages), so any change to the prompt, the
model, or the parameters is a natural cache MISS -- there is nothing to invalidate and
nothing goes stale. Because everything in the agent downstream of the model is
deterministic given the model's outputs, a re-run with a populated cache reproduces the
whole run with ZERO API calls (the original gate-5 "re-run hits the cache" check), and
as a bonus makes a finished run cheaply reproducible.

The cache is best-effort: any filesystem error is swallowed so a misbehaving disk can
never break an agent run -- it just means a miss.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_ROOT = Path(".cache/llm")


def cache_key(model: str, max_tokens: int, messages: list[dict]) -> str:
    blob = json.dumps({"model": model, "max_tokens": max_tokens, "messages": messages},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CompletionCache:
    """A directory of <key>.json files, each holding one stored completion."""

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def get(self, key: str) -> str | None:
        try:
            return json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))["completion"]
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, completion: str) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f".{key}.tmp"
            tmp.write_text(json.dumps({"completion": completion}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.root / f"{key}.json")     # atomic publish
        except OSError:
            pass   # never fail a run because the cache could not be written
