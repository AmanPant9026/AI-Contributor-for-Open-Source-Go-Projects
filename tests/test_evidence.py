"""Tests for the execution-evidence ladder (phases/evidence.py). All pure string-in /
ranking-out — no Docker, no model."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from go_issue_agent.phases.evidence import (
    Evidence, parse_coverage, parse_compiler_files, parse_trace_files, rerank,
)

MOD = "github.com/go-playground/validator/v10"


def test_parse_coverage_strips_module_and_filters_zero_and_tests():
    profile = "\n".join([
        "mode: set",
        f"{MOD}/baked_in.go:100.20,105.3 4 1",          # executed
        f"{MOD}/regexes.go:23.1,23.40 1 3",             # executed
        f"{MOD}/util.go:10.1,12.2 2 0",                 # NOT executed (count 0)
        f"{MOD}/validator_test.go:5.1,6.2 1 9",         # test file -> excluded
        f"{MOD}/translations/en/en.go:1.1,2.2 1 1",     # subpackage, executed
    ])
    covered = set(parse_coverage(profile, MOD))
    assert covered == {"baked_in.go", "regexes.go", "translations/en/en.go"}


def test_parse_compiler_files():
    out = "# pkg\n./baked_in.go:512:6: undefined: foo\nutil.go:3:1: syntax error\n"
    assert parse_compiler_files(out) == ["baked_in.go", "util.go"]


def test_parse_trace_files_keeps_repo_frames_deepest_first():
    known = ["baked_in.go", "validator.go", "util.go"]
    trace = (
        "panic: runtime error: invalid memory address\n"
        "goroutine 1 [running]:\n"
        f"\t{MOD}/util.go:88 +0x2a\n"               # deepest repo frame
        "\t/usr/local/go/src/reflect/value.go:1234 +0x5\n"   # stdlib -> dropped
        f"\t{MOD}/validator.go:315 +0x1c\n"
    )
    out = parse_trace_files(trace, known)
    assert out == ["util.go", "validator.go"]       # repo frames only, in trace order


def test_rerank_ladder_order():
    loc = ["baked_in.go", "errors.go", "regexes.go", "doc.go"]   # prose ranking
    ev = Evidence(covered=["regexes.go", "baked_in.go", "cache.go"])  # what executed
    ranked = rerank(loc, ev)
    # covered files first, ORDERED by their prose position (baked_in before regexes),
    # then covered-but-unranked (cache.go), then the prose fallback tail
    assert ranked[:3] == ["baked_in.go", "regexes.go", "cache.go"]
    assert "errors.go" in ranked and "doc.go" in ranked          # prose tail retained
    assert ranked.index("errors.go") > ranked.index("cache.go")  # fallback after covered


def test_rerank_compiler_and_trace_outrank_coverage():
    loc = ["a.go", "b.go", "c.go"]
    ev = Evidence(covered=["a.go", "b.go", "c.go"], traced=["c.go"], compiled_err=["b.go"])
    ranked = rerank(loc, ev)
    assert ranked[0] == "b.go"   # compiler error wins (rung 10)
    assert ranked[1] == "c.go"   # then trace (rung 9-8)


def test_rerank_no_evidence_returns_prose_order():
    loc = ["a.go", "b.go", "c.go"]
    assert rerank(loc, Evidence()) == loc        # empty evidence -> unchanged prose ranking
