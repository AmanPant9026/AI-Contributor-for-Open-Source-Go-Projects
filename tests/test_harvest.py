"""Tests for the Stage-6 data pipeline -- the harvester's pure logic (diff reconstruction,
splitting, test-func extraction, issue linking, instance assembly, size filtering). No network: the harvester's GitHub calls are exercised live via
the CLI, but the parsing/assembly that decides instance quality is tested here offline."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import harvest_repo as hv          # noqa: E402
from repos import REGISTRY         # noqa: E402

# A realistic GitHub /files payload: one code file fixed, one test file adding a test.
FILES = [
    {"filename": "command.go", "status": "modified",
     "patch": "@@ -10,3 +10,3 @@ func (c *Command) Execute() {\n \tctx := c.ctx\n-\treturn run(ctx, false)\n+\treturn run(ctx, true)\n }"},
    {"filename": "command_test.go", "status": "modified",
     "patch": "@@ -1,2 +1,7 @@\n package cobra\n+\n+func TestExecutePassesFlag(t *testing.T) {\n+\tif !lastFlag {\n+\t\tt.Fatal(\"flag not passed\")\n+\t}\n+}"},
    {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new"},  # dropped
]


def test_reconstruct_file_diff_modes():
    mod = hv.reconstruct_file_diff({"filename": "a.go", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"})
    assert mod.startswith("diff --git a/a.go b/a.go\n--- a/a.go\n+++ b/a.go\n")
    added = hv.reconstruct_file_diff({"filename": "n.go", "status": "added", "patch": "@@ -0,0 +1 @@\n+x"})
    assert "--- /dev/null\n+++ b/n.go" in added
    removed = hv.reconstruct_file_diff({"filename": "d.go", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"})
    assert "+++ /dev/null" in removed
    assert hv.reconstruct_file_diff({"filename": "b.go", "status": "modified", "patch": None}) is None
    assert hv.reconstruct_file_diff({"filename": "r.go", "status": "renamed", "patch": "x"}) is None


def test_split_files_classifies_code_test_and_drops_nongo():
    fix, test, touched = hv.split_files(FILES)
    assert "command.go" in fix and "command_test.go" not in fix
    assert "command_test.go" in test and "command.go b/command.go" not in test
    assert touched == ["command.go", "command_test.go"]      # README dropped
    assert "README" not in fix and "README" not in test


def test_added_test_funcs_and_code_lines():
    fix, test, _ = hv.split_files(FILES)
    assert hv.added_test_funcs(test) == ["TestExecutePassesFlag"]
    assert hv.diff_code_lines(fix) == 2          # one '-' and one '+' line in command.go


def test_linked_issue_number_prefers_fixes_keyword():
    assert hv.linked_issue_number("Fix execute", "This fixes #2090, see also #1") == 2090
    assert hv.linked_issue_number("title", "relates to #42") == 42
    assert hv.linked_issue_number("no refs", "") is None


def test_build_instance_shape():
    fix, test, _ = hv.split_files(FILES)
    pr = {"number": 2095, "base": {"sha": "abc123"}, "html_url": "u"}
    inst = hv.build_instance(REGISTRY["cobra"], pr, fix, test, "the bug", "issue#2090")
    assert inst["instance_id"] == "cobra-2095"
    assert inst["repo"] == "spf13/cobra"
    assert inst["base_commit"] == "abc123"
    assert inst["FAIL_TO_PASS"] == ["TestExecutePassesFlag"]
    assert inst["_needs_validation"] is True and inst["_source"] == "harvest"
    assert inst["_problem_source"] == "issue#2090"
    assert inst["patch"] == fix and inst["test_patch"] == test    # gold embedded for the harness


# --------------------------------------------------------------- small/medium size filter

def test_code_files_in_diff_excludes_tests():
    import harvest_repo as h
    diff = ("diff --git a/cmd/root.go b/cmd/root.go\n--- a/cmd/root.go\n+++ b/cmd/root.go\n"
            "@@\n-old\n+new\n"
            "diff --git a/cmd/root_test.go b/cmd/root_test.go\n--- a/cmd/root_test.go\n"
            "+++ b/cmd/root_test.go\n@@\n+func TestX(t *testing.T){}\n")
    assert h.code_files_in_diff(diff) == ["cmd/root.go"]      # _test.go excluded


def test_is_small_fix_filter():
    small = ("diff --git a/a.go b/a.go\n--- a/a.go\n+++ b/a.go\n@@\n-x\n+y\n+z\n")
    assert hv.is_small_fix(small, max_files=2, max_code_lines=50)
    # two code files -> fails a max_files=1 budget, passes max_files=2
    two = small + ("diff --git a/b.go b/b.go\n--- a/b.go\n+++ b/b.go\n@@\n+q\n")
    assert not hv.is_small_fix(two, max_files=1, max_code_lines=50)
    assert hv.is_small_fix(two, max_files=2, max_code_lines=50)
    # too many changed lines -> excluded even if one file
    big = "diff --git a/a.go b/a.go\n--- a/a.go\n+++ b/a.go\n@@\n" + "+line\n" * 60
    assert not hv.is_small_fix(big, max_files=2, max_code_lines=50)
    # a test-only diff is not a bug fix
    test_only = ("diff --git a/a_test.go b/a_test.go\n--- a/a_test.go\n+++ b/a_test.go\n@@\n+t\n")
    assert not hv.is_small_fix(test_only, max_files=2, max_code_lines=50)


def test_well_formed_issue_requires_linked_issue_and_body():
    good = "Validation panics on nil pointer\n\nWhen I call Struct() with a nil embedded pointer it panics instead of returning an error. Steps: ..."
    assert hv.well_formed_issue(good, "issue#42", min_chars=120)
    # title-only PR -> not issue-backed -> rejected
    assert not hv.well_formed_issue("Fix the thing", "pr_title_only", min_chars=120)
    # linked issue but trivially short body -> rejected
    assert not hv.well_formed_issue("broken\n\nplz fix", "issue#7", min_chars=120)
    # linked issue, single line only (no body) -> rejected
    assert not hv.well_formed_issue("just a title and nothing else here at all really", "issue#9", min_chars=20)
