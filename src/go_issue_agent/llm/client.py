"""Provider-agnostic LLM client. Stage 0: just enough to ping local Ollama via litellm.
The whole system talks to the model only through here, so swapping providers later
(hosted GLM-5.1, Claude, ...) is a one-line config change, not a code change.

Stage 4 adds a small injectable `LLMClient` (a `.complete(messages)->str` seam) so
the agent can be driven by a fake model in unit tests, with no Ollama required."""
from __future__ import annotations
import argparse
import os
import sys
import time

from ..config import settings
from .. import caching

# Substrings that mark a *transient* failure worth retrying (rate limits, server
# hiccups, timeouts, dropped connections). Auth/validation errors are NOT here, so
# they fail fast instead of pointlessly retrying.
_TRANSIENT_MARKERS = (
    "rate limit", "ratelimit", "rate_limit", "429", "overloaded",
    "500 ", "502", "503", "service unavailable",
    "timeout", "timed out", "temporarily", "try again",
    "connection", "econnreset",
)


def _is_transient(err: Exception) -> bool:
    s = str(err).lower()
    return any(m in s for m in _TRANSIENT_MARKERS)


def _retry_transient(fn, *, max_retries: int = 4, base_delay: float = 1.0, _sleep=time.sleep):
    """Call fn(); on a TRANSIENT error retry with exponential backoff (1, 2, 4, 8s).
    Non-transient errors (bad key, bad request, ...) raise immediately. `_sleep` is
    injectable so tests don't actually wait."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if _is_transient(e) and attempt < max_retries:
                _sleep(base_delay * (2 ** attempt))
                attempt += 1
                continue
            raise


def _completion_once(kwargs: dict, temperature: float):
    """One litellm attempt: send `temperature`, but retry once WITHOUT it for models
    that reject the parameter (some newer models, e.g. Claude Opus 4.x, deprecate it)."""
    import litellm  # lazy import so unit tests need not have it installed
    try:
        return litellm.completion(temperature=temperature, **kwargs)
    except Exception as e:  # noqa: BLE001
        if "temperature" in str(e).lower():
            return litellm.completion(**kwargs)   # retry without the unsupported param
        raise


def _call_litellm(kwargs: dict, temperature: float):
    """litellm call with transient-error retry/backoff; the temperature fallback is
    handled per attempt by `_completion_once`."""
    return _retry_transient(lambda: _completion_once(kwargs, temperature))


def _litellm_completion(model: str, api_base: str | None, messages: list[dict],
                        temperature: float, max_tokens: int) -> str:
    kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
    if api_base:                       # only Ollama needs an explicit base; hosted APIs route by name
        kwargs["api_base"] = api_base
    resp = _call_litellm(kwargs, temperature)
    return resp.choices[0].message.content or ""


def _api_base_for(model: str) -> str | None:
    """Ollama models talk to a local server; hosted models (anthropic/*, gpt-*, ...)
    must NOT be given that base or litellm will misroute them to localhost."""
    return settings.llm_api_base if model.startswith("ollama/") else None


class LLMClient:
    """Thin, swappable chat client. Inject a fake `completion_fn` in tests.

    Real model calls are cached on disk (content-addressed) so an unchanged re-run makes
    zero API calls; caching auto-disables when a fake `completion_fn` is injected (a fake
    is already cheap and deterministic, and a shared on-disk cache would otherwise leak
    between test runs). A `tracer`, if attached, records every call with cache hit/miss.
    """

    def __init__(self, *, model: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 1024, completion_fn=None,
                 use_cache: bool | None = None, cache_dir: str | None = None,
                 tracer=None) -> None:
        self.model = model or settings.llm_model
        self.api_base = _api_base_for(self.model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._completion_fn = completion_fn or _litellm_completion
        # cache real calls by default; never cache an injected fake (avoids cross-run leakage)
        if use_cache is None:
            use_cache = completion_fn is None and os.environ.get("GO_AGENT_LLM_CACHE", "1") != "0"
        self._use_cache = use_cache
        self._cache = caching.CompletionCache(cache_dir or caching.DEFAULT_ROOT)
        self._tracer = tracer

    def attach_tracer(self, tracer) -> None:
        self._tracer = tracer

    def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        """Send chat messages, return the assistant text (cached for real calls)."""
        mt = max_tokens or self.max_tokens
        key = caching.cache_key(self.model, mt, messages) if self._use_cache else None
        if key is not None:
            hit = self._cache.get(key)
            if hit is not None:
                self._trace_call(messages, mt, "hit")
                return hit
        out = self._completion_fn(self.model, self.api_base, messages, self.temperature, mt)
        if key is not None:
            self._cache.put(key, out)
        self._trace_call(messages, mt, "miss" if self._use_cache else "off")
        return out

    def _trace_call(self, messages: list[dict], max_tokens: int, cache_state: str) -> None:
        if self._tracer is not None:   # record the fact of the call, not the (large) content
            self._tracer.tool_call(
                "llm", model=self.model, max_tokens=max_tokens, cache=cache_state,
                input_chars=sum(len(m.get("content", "")) for m in messages))


def complete(prompt: str, *, model: str | None = None,
             temperature: float = 0.0, max_tokens: int = 256) -> str:
    m = model or settings.llm_model
    kwargs = dict(model=m, messages=[{"role": "user", "content": prompt}],
                  max_tokens=max_tokens)
    if m.startswith("ollama/"):
        kwargs["api_base"] = settings.llm_api_base
    resp = _call_litellm(kwargs, temperature)
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
