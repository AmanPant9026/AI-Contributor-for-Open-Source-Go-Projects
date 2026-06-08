"""Tests for the repair-rework pieces: model ranking of the coverage-narrowed shortlist
(repair.rank_suspects) and loading a target file's exact text (context.file_excerpt).
No Docker; rank uses a fake LLMClient, file_excerpt uses temp files."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from go_issue_agent.llm.client import LLMClient
from go_issue_agent.phases import repair
from go_issue_agent.phases.context import file_excerpt


def _llm(reply: str) -> LLMClient:
    return LLMClient(completion_fn=lambda model, api_base, messages, temperature, max_tokens: reply)


def test_rank_suspects_orders_by_model_then_keeps_the_rest():
    cands = ["baked_in.go", "validator.go", "cache.go", "regexes.go", "util.go"]
    # model names the quiet-but-correct file first, then a second
    ranked = repair.rank_suspects(_llm("regexes.go\nbaked_in.go"), "bug", "ctx", cands)
    assert ranked[0] == "regexes.go"
    assert ranked[1] == "baked_in.go"
    assert set(ranked) == set(cands)            # every candidate retained
    assert ranked[2:] == ["validator.go", "cache.go", "util.go"]   # unmentioned keep input order


def test_rank_suspects_falls_back_to_input_order_on_junk_reply():
    cands = ["a.go", "b.go", "c.go"]
    assert repair.rank_suspects(_llm("I am not sure, sorry!"), "bug", "ctx", cands) == cands


def test_rank_suspects_single_candidate_skips_model():
    # one candidate -> no model call needed, returned as-is
    assert repair.rank_suspects(_llm("should not be used"), "bug", "ctx", ["only.go"]) == ["only.go"]


def test_file_excerpt_small_file_included_whole(tmp_path):
    src = "package main\n\nfunc Greet(name string) string {\n\treturn \"hi \" + name\n}\n"
    (tmp_path / "main.go").write_text(src, encoding="utf-8")
    out = file_excerpt(tmp_path, "main.go", ["Greet"])
    assert "TARGET FILE: main.go" in out
    assert "func Greet(name string) string" in out      # exact text present to copy verbatim


def test_file_excerpt_missing_file_returns_empty(tmp_path):
    assert file_excerpt(tmp_path, "does_not_exist.go", ["x"]) == ""


def test_file_excerpt_large_file_windows_around_term_hit(tmp_path):
    # a >whole_below file where the bug site is a package-level var (not a func), so it is
    # reached via the term-hit window, not symbol matching -- the regexes.go scenario.
    filler = "".join(f"func f{i}() int {{ return {i} }}\n" for i in range(450))
    src = "package p\n\n" + filler + '\nvar phoneRegex = "^bad$"\n'
    (tmp_path / "big.go").write_text(src, encoding="utf-8")
    out = file_excerpt(tmp_path, "big.go", ["phoneRegex"], whole_below=100)
    assert "TARGET FILE: big.go" in out
    assert "phoneRegex" in out                          # the var line surfaced via hit-window
    assert "func f0()" not in out                       # NOT the whole file (it's large)
