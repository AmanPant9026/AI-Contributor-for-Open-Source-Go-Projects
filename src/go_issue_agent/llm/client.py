"""Provider-agnostic LLM client. Stage 0: just enough to ping local Ollama via litellm.
The whole system talks to the model only through here, so swapping providers later
(hosted GLM-5.1, Claude, ...) is a one-line config change, not a code change.

Stage 4 adds a small injectable `LLMClient` (a `.complete(messages)->str` seam) so
the agent can be driven by a fake model in unit tests, with no Ollama required."""
from __future__ import annotations
import argparse
import sys

from ..config import settings


def _litellm_completion(model: str, api_base: str | None, messages: list[dict],
                        temperature: float, max_tokens: int) -> str:
    import litellm  # lazy import so unit tests need not have it installed
    kwargs = dict(model=model, messages=messages,
                  temperature=temperature, max_tokens=max_tokens)
    if api_base:                       # only Ollama needs an explicit base; hosted APIs route by name
        kwargs["api_base"] = api_base
    resp = litellm.completion(**kwargs)
    return resp.choices[0].message.content or ""


def _api_base_for(model: str) -> str | None:
    """Ollama models talk to a local server; hosted models (anthropic/*, gpt-*, ...)
    must NOT be given that base or litellm will misroute them to localhost."""
    return settings.llm_api_base if model.startswith("ollama/") else None


class LLMClient:
    """Thin, swappable chat client. Inject a fake `completion_fn` in tests."""

    def __init__(self, *, model: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 1024, completion_fn=None) -> None:
        self.model = model or settings.llm_model
        self.api_base = _api_base_for(self.model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._completion_fn = completion_fn or _litellm_completion

    def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        """Send chat messages, return the assistant text."""
        return self._completion_fn(
            self.model, self.api_base, messages,
            self.temperature, max_tokens or self.max_tokens,
        )


def complete(prompt: str, *, model: str | None = None,
             temperature: float = 0.0, max_tokens: int = 256) -> str:
    import litellm
    m = model or settings.llm_model
    kwargs = dict(model=m, messages=[{"role": "user", "content": prompt}],
                  temperature=temperature, max_tokens=max_tokens)
    if m.startswith("ollama/"):
        kwargs["api_base"] = settings.llm_api_base
    resp = litellm.completion(**kwargs)
    return resp.choices[0].message.content or ""


def ping() -> str:
    """Tiny round-trip to confirm the model is reachable and answering."""
    return complete("Reply with the single word: pong", max_tokens=8)


def _main() -> None:
    ap = argparse.ArgumentParser(prog="llm.client")
    ap.add_argument("--ping", action="store_true", help="verify the model is reachable")
    ap.add_argument("--prompt", type=str, help="send a custom prompt")
    args = ap.parse_args()
    try:
        if args.ping:
            out = ping()
        elif args.prompt:
            out = complete(args.prompt)
        else:
            ap.print_help()
            return
        print(f"[llm] model={settings.llm_model} api_base={settings.llm_api_base}")
        print(f"[llm] response: {out.strip()!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[llm] ERROR talking to the model: {e}", file=sys.stderr)
        print("[llm] is `ollama serve` running and the model pulled? "
              "check LLM_MODEL / LLM_API_BASE in your .env", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
