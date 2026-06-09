"""Tests for the gold-validation gate's pure logic (parse_go_test_v, decide_validation) and
the multi-repo clone routing (repos.resolve_clone). The Docker orchestration in run_eval is
verified on a machine with Docker; the decision logic that determines validity is tested here."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import goldgate as gg          # noqa: E402
import repos                   # noqa: E402

GO_TEST_V = """\
=== RUN   TestAlpha
--- PASS: TestAlpha (0.00s)
=== RUN   TestBeta
    beta_test.go:9: boom
--- FAIL: TestBeta (0.01s)
=== RUN   TestGamma/sub
--- PASS: TestGamma/sub (0.00s)
FAIL
"""


def test_parse_go_test_v():
    res = gg.parse_go_test_v(GO_TEST_V)
    assert res == {"TestAlpha": "PASS", "TestBeta": "FAIL", "TestGamma/sub": "PASS"}


def test_decide_validation_keeps_real_fail_to_pass():
    keep, reason, f2p, p2p = gg.decide_validation(
        f2p_at_base={"TestBug": "FAIL"},
        f2p_after_fix={"TestBug": "PASS"},
        p2p_after_fix={"TestExisting": "PASS", "TestFlaky": "FAIL"})
    assert keep and reason == "validated"
    assert f2p == ["TestBug"]
    assert p2p == ["TestExisting"]            # only tests passing after the fix


def test_decide_validation_rejects_test_that_passes_at_base():
    keep, reason, *_ = gg.decide_validation(
        f2p_at_base={"TestBug": "PASS"},       # ran, but passed -> doesn't catch the bug
        f2p_after_fix={"TestBug": "PASS"}, p2p_after_fix={})
    assert not keep and "passes at base" in reason


def test_decide_validation_flags_compile_error_at_base_distinctly():
    # The test never appears in base output (package didn't compile) -> NEW feature, not a bug.
    keep, reason, *_ = gg.decide_validation(
        f2p_at_base={},                        # absent: did not run at base
        f2p_after_fix={"TestNewFeature": "PASS"}, p2p_after_fix={})
    assert not keep and "did not run at base" in reason


def test_decide_validation_rejects_fix_that_doesnt_fix():
    keep, reason, *_ = gg.decide_validation(
        f2p_at_base={"TestBug": "FAIL"},
        f2p_after_fix={"TestBug": "FAIL"},     # gold fix didn't make it pass
        p2p_after_fix={})
    assert not keep and "does not make F2P pass" in reason


def test_decide_validation_rejects_when_no_tests_ran():
    keep, reason, *_ = gg.decide_validation({}, {}, {})
    assert not keep and "no FAIL_TO_PASS" in reason


# --------------------------------------------------------------- multi-repo routing

def test_resolve_clone_concrete_repo():
    dest, url = repos.resolve_clone("cobra-2099", "spf13/cobra")
    assert dest == repos.REGISTRY["cobra"].clone_dir
    assert url == "https://github.com/spf13/cobra"


def test_resolve_clone_gin():
    dest, url = repos.resolve_clone("gin-3912", "gin-gonic/gin")
    assert dest == repos.REGISTRY["gin"].clone_dir and url.endswith("/gin-gonic/gin")


def test_resolve_clone_validator_unchanged():
    dest, url = repos.resolve_clone("validator-1314", "go-playground/validator")
    assert dest.name == "validator" and url.endswith("/go-playground/validator")

