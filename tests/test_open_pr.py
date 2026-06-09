"""Tests for the PR contributor's pure logic (eval/open_pr.py). The git + REST steps run on a
machine with git/network; here we test branch naming, changed-file parsing, PR title/body
generation (issue reference + the mandatory AI disclosure), and the refusal to open a PR for an
empty/abstained patch."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import open_pr as op   # noqa: E402

PATCH = ("diff --git a/recovery.go b/recovery.go\n--- a/recovery.go\n+++ b/recovery.go\n"
         "@@\n-old\n+new\n"
         "diff --git a/recovery_test.go b/recovery_test.go\n--- a/recovery_test.go\n"
         "+++ b/recovery_test.go\n@@\n+func TestX(t *testing.T){}\n")

INST = {"instance_id": "gin-4336", "_issue": 4336,
        "problem_statement": "Recovery middleware suppresses http.ErrAbortHandler\n\nlong body..."}


def test_branch_name():
    assert op.branch_name("validator-1476") == "fix/validator-1476"


def test_changed_files_lists_both():
    assert op.changed_files(PATCH) == ["recovery.go", "recovery_test.go"]


def test_pr_title_uses_first_line_and_issue():
    t = op.pr_title(INST)
    assert t.startswith("fix: Recovery middleware suppresses")
    assert t.endswith("(#4336)")


def test_pr_title_truncates_long_first_line():
    inst = {"instance_id": "x-1", "problem_statement": "x" * 200}
    assert len(op.pr_title(inst)) <= 80 and op.pr_title(inst).endswith("...")


def test_pr_body_has_issue_ref_validation_and_disclosure():
    body = op.pr_body(INST, ["recovery.go"])
    assert "Fixes #4336" in body
    assert "go build" in body and "go vet" in body and "reproduction test" in body
    # the AI disclosure is mandatory and must always be present
    assert "AI-assistance disclosure" in body
    assert "reviewed" in body.lower()


def test_pr_body_without_issue_has_no_fixes_line_but_keeps_disclosure():
    body = op.pr_body({"instance_id": "x-1"}, ["a.go"])
    assert "Fixes #" not in body
    assert "AI-assistance disclosure" in body


def test_read_patch_refuses_missing(tmp_path):
    with pytest.raises(SystemExit):
        op.read_patch("nope-1", str(tmp_path / "missing.patch"))


def test_read_patch_refuses_empty(tmp_path):
    p = tmp_path / "empty.patch"
    p.write_text("   \n")
    with pytest.raises(SystemExit):
        op.read_patch("empty-1", str(p))


def test_read_patch_returns_content(tmp_path):
    p = tmp_path / "ok.patch"
    p.write_text(PATCH)
    assert op.read_patch("ok-1", str(p)) == PATCH


def test_pr_title_strips_redundant_fix_prefix():
    inst = {"instance_id": "validator-1476", "_issue": 1476,
            "problem_statement": "Fix: validation now rejects phone codes starting with +0"}
    t = op.pr_title(inst)
    assert t == "fix: validation now rejects phone codes starting with +0 (#1476)"
    assert "Fix:" not in t            # no doubled prefix
