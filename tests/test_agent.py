"""Stage 4 unit tests -- deterministic, no Ollama and no Docker.

The LLM is a scripted fake (routes on prompt content) and validation is injected,
so we exercise the full localize->context->repro->repair->finalize spine and the
edit machinery without any external service.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from go_issue_agent import edits  # noqa: E402
from go_issue_agent.agent import run_agent  # noqa: E402
from go_issue_agent.llm.client import LLMClient  # noqa: E402
from go_issue_agent.phases import localize, context as context_phase  # noqa: E402
from go_issue_agent.phases.localize import Localization  # noqa: E402
from go_issue_agent.phases.validate import ValidationResult  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "minirepo"

# A real-tab SEARCH/REPLACE editing the fixture's core.go (Go uses tabs).
FIX_BLOCK = (
    "core.go\n"
    "<<<<<<< SEARCH\n"
    '\treturn fmt.Sprintf("hello %s", name)\n'
    "=======\n"
    '\treturn fmt.Sprintf("HELLO %s", name)\n'
    ">>>>>>> REPLACE\n"
)
# Path-free block (the format the model is now asked to produce).
FIX_BLOCK_NOPATH = (
    "<<<<<<< SEARCH\n"
    '\treturn fmt.Sprintf("hello %s", name)\n'
    "=======\n"
    '\treturn fmt.Sprintf("HELLO %s", name)\n'
    ">>>>>>> REPLACE\n"
)
REPRO_GO = (
    "package minirepo\n\nimport \"testing\"\n\n"
    "func TestAgentRepro(t *testing.T) {\n\tif Hello(\"x\") == \"\" {\n\t\tt.Fatal(\"empty\")\n\t}\n}\n"
)


def make_repo(tmp_path) -> tuple[Path, str]:
    """Copy the fixture into a fresh git repo; return (path, HEAD)."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return repo, head


def scripted_llm(*, fix_block: str = FIX_BLOCK_NOPATH, context_action: str = "DONE") -> LLMClient:
    """A fake model that routes on prompt content."""
    def fn(model, api_base, messages, temperature, max_tokens):
        text = messages[-1]["content"]
        if "ONE action per turn" in text:
            return context_action
        if "reproduction test" in text or "REPRODUCES the bug" in text:
            return REPRO_GO
        if "search/replace blocks" in text:
            return fix_block
        if "pull-request" in text:
            return "TITLE: Fix the greeting\nBODY:\nIt was wrong; now it is right."
        return ""
    return LLMClient(completion_fn=fn)


# ---------------------------------------------------------------- edits
def test_parse_single_block():
    es = edits.parse_edits(FIX_BLOCK)
    assert len(es) == 1 and es[0].path == "core.go"
    assert 'HELLO' in es[0].replace


def test_parse_multiple_blocks_and_fences():
    text = "```go\n" + FIX_BLOCK + "```\n" + FIX_BLOCK.replace("core.go", "a.go")
    es = edits.parse_edits(text)
    assert {e.path for e in es} == {"core.go", "a.go"}


def test_apply_edit_changes_file(tmp_path):
    repo, _ = make_repo(tmp_path)
    res = edits.apply_edits(repo, edits.parse_edits(FIX_BLOCK))
    assert res.ok and res.changed_files == ["core.go"]
    assert "HELLO" in (repo / "core.go").read_text()


def test_apply_edit_reports_no_match(tmp_path):
    repo, _ = make_repo(tmp_path)
    bad = "core.go\n<<<<<<< SEARCH\nthis text is not present\n=======\nx\n>>>>>>> REPLACE\n"
    res = edits.apply_edits(repo, edits.parse_edits(bad))
    assert not res.ok and "not found" in res.failures[0]


def test_apply_edit_creates_file_on_empty_search(tmp_path):
    repo, _ = make_repo(tmp_path)
    block = "new.go\n<<<<<<< SEARCH\n=======\npackage minirepo\n>>>>>>> REPLACE\n"
    res = edits.apply_edits(repo, edits.parse_edits(block))
    assert res.ok and (repo / "new.go").exists()


# ---------------------------------------------------------------- localize
def test_extract_terms_drops_stopwords_and_ranks_identifiers():
    terms = localize.extract_issue_terms(
        "The `Hello` function should return a greeting but returns empty for valid input.")
    assert "Hello" in terms
    assert "the" not in terms and "return" not in terms


def test_localize_points_at_central_file():
    loc = localize.localize(FIXTURE, "The `Hello` function is broken for some input.")
    assert "core.go" in loc.candidates[:3]


# ---------------------------------------------------------------- context loop
def test_parse_strips_angle_brackets():
    block = "<core.go>\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"
    es = edits.parse_edits(block)
    assert es and es[0].path == "core.go"


def test_parse_pathfree_block_has_no_path():
    es = edits.parse_edits(FIX_BLOCK_NOPATH)
    assert len(es) == 1 and es[0].path == ""


def test_apply_rejects_noop_edit(tmp_path):
    repo, _ = make_repo(tmp_path)
    # SEARCH == REPLACE -> no real change -> must be rejected, not counted as applied
    same = '\treturn fmt.Sprintf("hello %s", name)\n'
    block = f"<<<<<<< SEARCH\n{same}=======\n{same}>>>>>>> REPLACE\n"
    res = edits.apply_edits(repo, edits.parse_edits(block), default_target="core.go")
    assert res.applied == 0 and not res.ok


def test_apply_pathfree_uses_default_target(tmp_path):
    repo, _ = make_repo(tmp_path)
    res = edits.apply_edits(repo, edits.parse_edits(FIX_BLOCK_NOPATH), default_target="core.go")
    assert res.ok and res.changed_files == ["core.go"]
    assert "HELLO" in (repo / "core.go").read_text()


def test_apply_filename_placeholder_falls_back_to_default(tmp_path):
    repo, _ = make_repo(tmp_path)
    # model echoes the literal placeholder as the path -> cleaned to "" -> default used
    block = ("FILENAME\n<<<<<<< SEARCH\n"
             '\treturn fmt.Sprintf("hello %s", name)\n=======\n'
             '\treturn fmt.Sprintf("HELLO %s", name)\n>>>>>>> REPLACE\n')
    res = edits.apply_edits(repo, edits.parse_edits(block), default_target="core.go")
    assert res.ok and "HELLO" in (repo / "core.go").read_text()


def test_apply_resolves_mangled_path(tmp_path):
    repo, _ = make_repo(tmp_path)
    block = ("pkg/wrong/core.go\n<<<<<<< SEARCH\n"
             '\treturn fmt.Sprintf("hello %s", name)\n=======\n'
             '\treturn fmt.Sprintf("HELLO %s", name)\n>>>>>>> REPLACE\n')
    res = edits.apply_edits(repo, edits.parse_edits(block))
    assert res.ok and res.changed_files == ["core.go"]


def test_terms_filter_panic_addresses():
    # hex addresses from a panic trace must not become "terms"
    terms = localize.extract_issue_terms(
        "panic at 0x1400008f550 and 0x10137e740 in `validateStruct` for PrivateField")
    assert not any(t.startswith("x1") or t.startswith("0x") for t in terms)
    assert "validateStruct" in terms or "PrivateField" in terms


def test_focus_snippets_includes_named_function():
    from go_issue_agent.phases import context as ctx
    snip = ctx.focus_snippets(FIXTURE, ["Hello"], ["core.go"])
    assert "func Hello(name string) string" in snip
    assert "// FILE: core.go" in snip


def test_context_gather_dedups_repeats():
    # model keeps issuing the SAME action -> loop must stop, not spin to the budget
    llm = scripted_llm(context_action="READ core.go 1 8")
    loc = Localization(terms=["Hello"], candidates=["core.go"], repo_map_skeleton="core.go")
    ctx = context_phase.gather(llm, FIXTURE, loc, "Hello is broken", max_reads=5)
    assert len(ctx.actions) == 2                  # de-duplicated, bounded
    assert "func Hello" in ctx.text               # exact source present via focus_snippets


# ---------------------------------------------------------------- full agent
def test_agent_resolves_with_fake_validate(tmp_path):
    repo, head = make_repo(tmp_path)
    ok_validate = lambda rd, code: ValidationResult(True, "all")
    res = run_agent("The `Hello` function returns the wrong greeting.", repo,
                    llm=scripted_llm(), base_ref=head, validate_fn=ok_validate)
    assert res.status == "resolved_internally"
    assert res.internal_ok and res.attempts == 1
    assert "HELLO" in res.code_patch              # the code fix is in the patch
    assert "_test.go" not in res.code_patch       # repro test excluded
    assert res.pr_title == "Fix the greeting"
    assert "TestAgentRepro" in res.repro_code


def test_agent_regenerates_repro_until_it_reproduces(tmp_path):
    # first repro doesn't fail on base (invalid) -> regenerate; second one does -> proceed
    repo, head = make_repo(tmp_path)
    calls = {"n": 0}
    def reproduces(rd, code):
        calls["n"] += 1
        return calls["n"] >= 2                      # invalid first, valid second
    res = run_agent("The `Hello` function is broken.", repo,
                    llm=scripted_llm(), base_ref=head,
                    validate_fn=lambda rd, code: ValidationResult(True, "all"),
                    reproduces_fn=reproduces)
    assert calls["n"] == 2                           # it regenerated once
    assert res.status == "resolved_internally" and "HELLO" in res.code_patch


def test_agent_abstains_when_repro_never_reproduces(tmp_path):
    # the model can never write a test that fails on base -> cannot verify -> no_repro, empty patch
    repo, head = make_repo(tmp_path)
    res = run_agent("The `Hello` function is broken.", repo,
                    llm=scripted_llm(), base_ref=head, max_repro_attempts=3,
                    validate_fn=lambda rd, code: ValidationResult(True, "all"),
                    reproduces_fn=lambda rd, code: False)
    assert res.status == "no_repro" and res.code_patch.strip() == ""


def test_agent_abstains_when_repro_fails(tmp_path):
    # build+vet may pass, but the agent's repro did NOT -> unverified -> abstain (empty patch).
    # The attempted fix is still kept for debugging, just not submitted.
    repo, head = make_repo(tmp_path)
    repro_fail = lambda rd, code: ValidationResult(False, "repro", "self-test failed")
    res = run_agent("The `Hello` function is broken.", repo,
                    llm=scripted_llm(), base_ref=head, max_repair_attempts=2,
                    validate_fn=repro_fail)
    assert res.status == "abstained"
    assert res.code_patch.strip() == "" and not res.internal_ok
    assert "HELLO" in res.attempt_patch            # visible for debugging, not submitted


def test_agent_abstains_when_build_fails(tmp_path):
    # build (objective) fails -> genuinely broken -> abstain with an EMPTY patch (do no harm)
    repo, head = make_repo(tmp_path)
    build_fail = lambda rd, code: ValidationResult(False, "build", "does not compile")
    res = run_agent("The `Hello` function is broken.", repo,
                    llm=scripted_llm(), base_ref=head, max_repair_attempts=2,
                    validate_fn=build_fail)
    assert res.status == "abstained"
    assert res.code_patch.strip() == "" and not res.internal_ok
    assert res.attempt_patch.strip() != ""         # the broken attempt is still visible for debug


def test_agent_submits_unvalidated_when_opted_in(tmp_path):
    # build fails, but opt-in -> submit the best (broken) attempt instead of abstaining
    repo, head = make_repo(tmp_path)
    build_fail = lambda rd, code: ValidationResult(False, "build", "does not compile")
    res = run_agent("The `Hello` function is broken.", repo,
                    llm=scripted_llm(), base_ref=head, max_repair_attempts=2,
                    validate_fn=build_fail, submit_unvalidated=True)
    assert res.status == "gave_up"
    assert "HELLO" in res.code_patch and not res.internal_ok


def test_agent_handles_no_edits(tmp_path):
    repo, head = make_repo(tmp_path)
    res = run_agent("Broken.", repo, llm=scripted_llm(fix_block="sorry, no idea"),
                    base_ref=head, max_repair_attempts=2,
                    validate_fn=lambda rd, code: ValidationResult(True, "all"))
    # no parseable edits -> empty patch -> no_edits
    assert res.status == "no_edits" and res.code_patch.strip() == ""


# ---------------------------------------------------------------- llm client seam
def test_llmclient_uses_injected_fn():
    llm = LLMClient(completion_fn=lambda *a: "pong")
    assert llm.complete([{"role": "user", "content": "ping"}]) == "pong"
