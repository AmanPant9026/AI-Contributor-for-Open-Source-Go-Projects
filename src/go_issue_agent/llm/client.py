"""Provider-agnostic LLM client. Stage 0: just enough to ping local Ollama via litellm.
The whole system talks to the model only through here, so swapping providers later
(hosted GLM-5.1, Claude, ...) is a one-line config change, not a code change."""
from __future__ import annotations
import argparse
import sys

import litellm

from ..config import settings


def complete(prompt: str, *, model: str | None = None,
             temperature: float = 0.0, max_tokens: int = 256) -> str:
    resp = litellm.completion(
        model=model or settings.llm_model,
        api_base=settings.llm_api_base,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
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
