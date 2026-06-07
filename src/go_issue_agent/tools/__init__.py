"""Stage 3 deterministic tools -- the agent's eyes and hands.

Each tool is a thin, unit-tested wrapper; no LLM and no agent loop live here
(that is Stage 4). See docs/stage-3.md.
"""
from . import fileio, read_span, search_code, apply_patch, go_tools  # noqa: F401
