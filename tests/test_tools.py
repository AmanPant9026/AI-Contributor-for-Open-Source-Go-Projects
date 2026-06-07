"""gate-3 -- unit tests for the Stage 3 deterministic tools.

No LLM and (except where noted) no Docker: go_tools' sandboxed runners are tested
by monkeypatching the runner, so the whole suite is fast and runs anywhere.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from go_issue_agent.tools import fileio, read_span, search_code, apply_patch, go_tools  # noqa: E402
from go_issue_agent.indexing import ast_nav, repo_map  # noqa: E402
from go_issue_agent.models import CommandResult  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "minirepo"


# ----------------------------------------------------------------- fileio
def test_list_files_go_only_and_sorted():
    files = fileio.list_files(FIXTURE, suffix=".go")
    assert files == sorted(files)
    assert "core.go" in files and "go.mod" not in files


def test_list_files_can_exclude_tests():
    with_tests = fileio.list_files(FIXTURE, suffix=".go", include_tests=True)
    without = fileio.list_files(FIXTURE, suffix=".go", include_tests=False)
    assert "core_test.go" in with_tests
    assert "core_test.go" not in without


def test_read_and_write_roundtrip(tmp_path):
    fileio.write_file(tmp_path, "sub/x.txt", "hello\n")
    assert fileio.read_file(tmp_path, "sub/x.txt") == "hello\n"


def test_path_escape_blocked(tmp_path):
    with pytest.raises(ValueError):
        fileio.read_file(tmp_path, "../../etc/passwd")


# ----------------------------------------------------------------- read_span
def test_read_span_inclusive_with_numbers():
    out = read_span.read_span(FIXTURE, "a.go", 3, 5)
    lines = out.splitlines()
    assert lines[0].startswith("3\t") and "func A()" in lines[0]
    assert len(lines) == 3


def test_read_span_clamps_out_of_range():
    out = read_span.read_span(FIXTURE, "a.go", 1, 9999, with_line_numbers=False)
    assert out.splitlines()[0] == "package minirepo"


# ----------------------------------------------------------------- search_code
def test_search_finds_symbol():
    hits = search_code.search_code(FIXTURE, "Hello")
    paths = {h.path for h in hits}
    assert {"core.go", "a.go", "b.go"} <= paths
    assert all(h.line > 0 for h in hits)


# ----------------------------------------------------------------- ast_nav
def test_parse_file_extracts_func_and_type():
    syms = {s.name: s for s in ast_nav.parse_file(FIXTURE / "core.go")}
    assert syms["Hello"].kind == "func"
    assert "func Hello(name string) string" in syms["Hello"].signature
    assert syms["Config"].kind == "type"
    assert syms["Hello"].start_line == 6


def test_find_definitions_skips_tests():
    defs = ast_nav.find_definitions(FIXTURE, "Hello")
    assert [p for p, _ in defs] == ["core.go"]


# ----------------------------------------------------------------- repo_map
def test_pagerank_ranks_central_file_first():
    rm = repo_map.build_repo_map(FIXTURE)
    assert rm.ranked_files[0].path == "core.go"


def test_repo_map_excludes_go_ignored_dirs():
    # _examples/demo.go must never appear in the map (Go ignores _-prefixed dirs)
    rm = repo_map.build_repo_map(FIXTURE)
    assert all(not f.path.startswith("_examples") for f in rm.ranked_files)
    assert "DemoOnly" not in rm.skeleton


def test_find_definitions_excludes_ignored_dirs():
    # DemoOnly is only defined under _examples -> must not be found
    assert ast_nav.find_definitions(FIXTURE, "DemoOnly") == []


def test_skeleton_has_signatures_no_bodies():
    rm = repo_map.build_repo_map(FIXTURE)
    assert "func Hello(name string) string" in rm.skeleton
    assert "[L6]" in rm.skeleton
    assert "fmt.Sprintf" not in rm.skeleton  # bodies are dropped


def test_budget_truncates():
    rm = repo_map.build_repo_map(FIXTURE, budget_tokens=1)
    assert rm.truncated is True


def test_pagerank_sums_to_one():
    _, defines = repo_map._collect(FIXTURE)
    files, _ = repo_map._collect(FIXTURE)
    graph = repo_map._build_graph(FIXTURE, files, defines)
    ranks = repo_map.pagerank(graph)
    assert abs(sum(ranks.values()) - 1.0) < 1e-6


# ----------------------------------------------------------------- apply_patch
def test_apply_patch_applies_real_diff(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "f.txt").write_text("one\ntwo\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "init"], check=True)
    (repo / "f.txt").write_text("one\nTWO\n")
    diff = subprocess.run(["git", "-C", str(repo), "diff"],
                          capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "f.txt"], check=True)

    res = apply_patch.apply_patch(repo, diff)
    assert res.applied and not res.empty
    assert (repo / "f.txt").read_text() == "one\nTWO\n"


def test_apply_patch_empty_is_noop(tmp_path):
    res = apply_patch.apply_patch(tmp_path, "   \n")
    assert res.empty and not res.applied


def test_reapply_already_applied_fails_cleanly(tmp_path):
    # Re-applying an already-applied patch must FAIL fast, never block on a
    # `patch` "Assume -R?" prompt (we feed DEVNULL + use -N in apply_patch).
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "f.txt").write_text("one\ntwo\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "init"], check=True)
    (repo / "f.txt").write_text("one\nTWO\n")
    diff = subprocess.run(["git", "-C", str(repo), "diff"],
                          capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "f.txt"], check=True)
    assert apply_patch.apply_patch(repo, diff).applied is True       # first time: applies
    assert apply_patch.apply_patch(repo, diff).applied is False      # second time: clean fail


def test_apply_patch_garbage_fails(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    res = apply_patch.apply_patch(tmp_path, "not a real diff\n")
    assert not res.applied and res.method == "failed"


# ----------------------------------------------------------------- go_tools
def test_go_command_builders():
    assert go_tools.build_cmd() == "go build ./..."
    assert go_tools.vet_cmd() == "go vet ./..."
    assert go_tools.test_cmd(["TestUrl"]) == "go test -run '^(TestUrl)$' ./..."
    assert go_tools.test_cmd(["A", "B"]) == "go test -run '^(A|B)$' ./..."
    assert go_tools.test_cmd() == "go test ./..."
    assert go_tools.gofmt_check_cmd([]) == "true"
    assert "gofmt -l 'x.go'" in go_tools.gofmt_check_cmd(["x.go"])


def test_go_tools_run_uses_sandbox(monkeypatch):
    seen = {}

    def fake_run(workspace, command, **kw):
        seen["cmd"] = command
        seen["mounts"] = kw.get("extra_mounts")
        return CommandResult(command, 0, "", "", 0.0)

    monkeypatch.setattr(go_tools, "run_in_sandbox", fake_run)
    res = go_tools.go_build("/tmp/repo")
    assert res.ok
    assert seen["cmd"] == "go build ./..."
    assert seen["mounts"] == [(".cache/gomod", "/go/pkg/mod")]
