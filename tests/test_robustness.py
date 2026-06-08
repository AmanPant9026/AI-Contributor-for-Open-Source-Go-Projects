"""gate-5 robustness suite -- the original Stage-5 checks, each provable without Docker
or a real model:

  1. the repair loop is bounded (engages, provably stops at N -- no infinite loop)
  2. selection submits a candidate that validated, when one exists (first-verified policy)
  3. a re-run hits the cache: identical inputs -> zero extra model calls, across a fresh
     client (persistence), and an injected fake is NOT cached (no cross-run leakage)
  4. the decision trace lists tool calls

Reliability invariants from Workstream B (timeout handled, transient-API recovers, one
instance crashing doesn't abort the run) are covered in tests/test_reliability.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # so we can reuse the test_agent harness

from go_issue_agent.agent import run_agent                  # noqa: E402
from go_issue_agent.llm.client import LLMClient             # noqa: E402
from go_issue_agent.phases.validate import ValidationResult  # noqa: E402
import test_agent as ta                                     # noqa: E402

ALWAYS_FAIL = lambda rd, code: ValidationResult(False, "repro", "still fails")   # noqa: E731
ALWAYS_OK = lambda rd, code: ValidationResult(True, "all")                       # noqa: E731


# ----------------------------------------------------------------- 1. bounded loop
def test_repair_loop_is_bounded_no_infinite_loop(tmp_path):
    repo, head = ta.make_repo(tmp_path)
    llm = ta.scripted_llm(fix_block=ta.FIX_BLOCK_NOPATH)
    res = run_agent("the greeting is wrong", repo, llm=llm, base_ref=head,
                    validate_fn=ALWAYS_FAIL, max_repair_attempts=3)
    # validate never passes -> the agent must STOP at the cap and abstain, not loop forever
    assert res.attempts <= 3
    assert res.status == "abstained"
    assert res.code_patch == ""        # do-no-harm: nothing unverified is submitted


# ------------------------------------------------- 2. selection picks a validated candidate
def test_selection_submits_a_validated_candidate(tmp_path):
    repo, head = ta.make_repo(tmp_path)
    llm = ta.scripted_llm(fix_block=ta.FIX_BLOCK_NOPATH)
    res = run_agent("the greeting is wrong", repo, llm=llm, base_ref=head,
                    validate_fn=ALWAYS_OK)
    assert res.status == "resolved_internally"
    assert res.internal_ok is True
    assert res.code_patch.strip()      # the validated candidate is what gets submitted


# ------------------------------------------------------------------------ 3. cache
def _counter():
    calls = {"n": 0}
    def fn(model, api_base, messages, temperature, max_tokens):
        calls["n"] += 1
        return "RESP"
    return fn, calls


def test_cache_repeat_is_zero_extra_calls_and_persists(tmp_path):
    fn, calls = _counter()
    cdir = str(tmp_path / "llmcache")
    c = LLMClient(completion_fn=fn, use_cache=True, cache_dir=cdir)
    msgs = [{"role": "user", "content": "hello"}]
    assert c.complete(msgs) == "RESP"
    assert c.complete(msgs) == "RESP"          # identical -> served from cache
    assert calls["n"] == 1
    c.complete([{"role": "user", "content": "different"}])   # different key -> real call
    assert calls["n"] == 2
    # a FRESH client on the same dir reproduces the first call from disk: the "re-run"
    c2 = LLMClient(completion_fn=fn, use_cache=True, cache_dir=cdir)
    assert c2.complete(msgs) == "RESP"
    assert calls["n"] == 2                       # zero new model calls on re-run


def test_injected_fake_is_not_cached_by_default(tmp_path):
    # safety: a fake completion_fn must NOT be cached by default, or test runs would leak
    # cached values between each other through the shared on-disk cache.
    fn, calls = _counter()
    c = LLMClient(completion_fn=fn, cache_dir=str(tmp_path / "c"))   # use_cache=None -> auto
    msgs = [{"role": "user", "content": "hi"}]
    c.complete(msgs)
    c.complete(msgs)
    assert calls["n"] == 2


# ------------------------------------------------------------------------ 4. trace
def test_trace_lists_tool_calls_and_decisions(tmp_path):
    repo, head = ta.make_repo(tmp_path)
    llm = ta.scripted_llm(fix_block=ta.FIX_BLOCK_NOPATH)
    res = run_agent("the greeting is wrong", repo, llm=llm, base_ref=head,
                    validate_fn=ALWAYS_OK)
    tr = res.trace
    assert tr["tool_call_count"] > 0
    names = {e["name"] for e in tr["events"]}
    kinds = {e["type"] for e in tr["events"]}
    assert "llm" in names                       # model calls recorded as tool calls
    assert "validate" in names                  # validation recorded as a tool call
    assert {"localize", "finalize"} <= names    # key decisions recorded
    assert {"tool_call", "decision"} <= kinds
    # every llm event carries a cache state (hit/miss/off) -- the trace doubles as cache proof
    assert all("cache" in e for e in tr["events"] if e["name"] == "llm")
