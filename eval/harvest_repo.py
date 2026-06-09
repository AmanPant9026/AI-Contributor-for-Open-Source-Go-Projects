"""Harvest SWE-bench-style candidate instances from a repo's merged PR history.

This is our own lightweight version of SWE-bench's data-collection procedure (their
own instance-creation tooling is currently constrained). For each recent merged PR that
touches BOTH a non-test `.go` file and a `_test.go` file, we:

  * take the PR's full unified diff and split it into fix.patch (code) + test.patch (tests),
  * use the PR's base commit as base_commit (the diff applies cleanly onto it),
  * derive a PROVISIONAL FAIL_TO_PASS from the test functions the PR adds, and
  * use the linked GitHub ISSUE as the problem statement when we can find one (the PR body
    can describe the solution, which would leak the fix -- so we prefer the issue and
    record which source we used).

What this produces are CANDIDATES (`_needs_validation: true`). The gold-validation gate
(run on a machine with Docker) confirms FAIL_TO_PASS by running gold+test in the sandbox
and only keeps instances that genuinely go FAIL -> PASS. Harvesting (HTTP) is done here;
deriving/validating (Docker) is done by the harness.

The pure functions (`split_unified_diff`, `added_test_funcs`, `diff_code_lines`,
`linked_issue_number`, `build_instance`) are unit-tested without network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from repos import REGISTRY, Repo  # noqa: E402

_API = "https://api.github.com"
_ADDED_TESTFUNC = re.compile(r"^\+func\s+((?:Test|Example|Fuzz|Benchmark)[A-Za-z0-9_]*)\s*\(")
_ISSUE_REF = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
_ANY_REF = re.compile(r"#(\d+)")


# ----------------------------------------------------------------- pure helpers

def reconstruct_file_diff(f: dict) -> str | None:
    """Build an applicable unified-diff block for one file from the GitHub `/files` entry
    (its `patch` is the hunks only, without the git header). Returns None for files with
    no textual patch (binary) or renames we don't handle."""
    fn, status, patch = f.get("filename", ""), f.get("status", ""), f.get("patch")
    if not patch or status == "renamed":
        return None
    old = "/dev/null" if status == "added" else f"a/{fn}"
    new = "/dev/null" if status == "removed" else f"b/{fn}"
    return f"diff --git a/{fn} b/{fn}\n--- {old}\n+++ {new}\n{patch}\n"


def split_files(files: list[dict]) -> tuple[str, str, list[str]]:
    """Split a PR's `/files` list into (fix_patch, test_patch, touched_go_paths): non-test
    `.go` files form the code patch, `_test.go` files the test patch; non-Go files dropped."""
    code, test, touched = [], [], []
    for f in files:
        fn = f.get("filename", "")
        if not fn.endswith(".go"):
            continue
        block = reconstruct_file_diff(f)
        if block is None:
            continue
        touched.append(fn)
        (test if fn.endswith("_test.go") else code).append(block)
    return "".join(code), "".join(test), touched


def added_test_funcs(test_patch: str) -> list[str]:
    """Test/Example/Fuzz/Benchmark functions ADDED by the test patch (provisional F2P)."""
    out = []
    for line in test_patch.splitlines():
        m = _ADDED_TESTFUNC.match(line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def diff_code_lines(fix_patch: str) -> int:
    """Added+removed source lines in the code patch (a size proxy to skip huge PRs)."""
    return sum(1 for ln in fix_patch.splitlines()
               if (ln.startswith("+") or ln.startswith("-"))
               and not ln.startswith(("+++", "---")))


def code_files_in_diff(diff: str) -> list[str]:
    """Non-test `.go` files touched by a unified diff (the 'how localized is the fix' proxy)."""
    out = []
    for ln in diff.splitlines():
        if ln.startswith("+++ b/"):
            p = ln[6:].strip()
            if p.endswith(".go") and not p.endswith("_test.go"):
                out.append(p)
    return sorted(set(out))


def is_small_fix(fix_patch: str, max_files: int, max_code_lines: int) -> bool:
    """A localized, bounded code change -- the assignment's 'small or medium issue'. Requires at
    least one non-test code file (a test-only diff is not a bug fix)."""
    nf = len(code_files_in_diff(fix_patch))
    return 1 <= nf <= max_files and diff_code_lines(fix_patch) <= max_code_lines


def linked_issue_number(title: str, body: str) -> int | None:
    """The issue a PR closes: prefer an explicit 'fixes #N', else the first '#N' seen."""
    text = f"{title}\n{body or ''}"
    m = _ISSUE_REF.search(text) or _ANY_REF.search(text)
    return int(m.group(1)) if m else None


def well_formed_issue(problem_statement: str, source: str, min_chars: int) -> bool:
    """A 'validator-quality' question: backed by a real linked ISSUE (not just a PR title) whose
    text is substantial enough to reproduce from -- title plus a body of at least `min_chars`.
    This is a COARSE pre-screen; a human still eyeballs each kept issue before the set is locked."""
    if not source.startswith("issue#"):
        return False
    text = problem_statement.strip()
    nonempty_lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(nonempty_lines) >= 2 and len(text) >= min_chars


def build_instance(repo: Repo, pr: dict, fix_patch: str, test_patch: str,
                   problem_statement: str, problem_source: str) -> dict:
    f2p = added_test_funcs(test_patch)
    return {
        "instance_id": f"{repo.key}-{pr['number']}",
        "repo": repo.slug,
        "base_commit": pr["base"]["sha"],
        "problem_statement": problem_statement.strip(),
        "patch": fix_patch,           # gold code fix (the eval harness reads this)
        "test_patch": test_patch,     # gold test
        "FAIL_TO_PASS": f2p,          # provisional; confirmed by the Docker gold-gate
        "PASS_TO_PASS": [],           # derived by the gold-gate
        "_source": "harvest",
        "_problem_source": problem_source,
        "_pr": pr["number"],
        "_pr_url": pr.get("html_url", ""),
        "_code_lines": diff_code_lines(fix_patch),
        "_needs_validation": True,
    }


# ----------------------------------------------------------------- GitHub REST (live)

def _get(url: str, token: str | None, *, retries: int = 3):
    hdr = {"User-Agent": "go-issue-agent-harvester",
           "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace").lower()
            if e.code == 403 and "rate limit" in body:
                raise RuntimeError("GitHub rate limit hit -- set GITHUB_TOKEN for 5000/hr") from e
            if e.code in (502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def harvest(repo: Repo, *, limit: int, max_code_lines: int, token: str | None,
            out_dir: Path, scan: int, max_files: int = 2,
            require_issue: bool = False, min_issue_chars: int = 120) -> list[dict]:
    """Scan up to `scan` recent merged PRs (paginating as needed), keep up to `limit` that look
    like fix+test candidates (<= max_code_lines, <= max_files code files), write each to
    out_dir/<id>/. With require_issue, keep ONLY PRs backed by a substantial linked issue
    (validator-quality questions) and print each issue for human review."""
    kept: list[dict] = []
    seen = 0
    page = 1
    while seen < scan and len(kept) < limit:
        per_page = min(100, scan - seen)
        batch = _get(f"{_API}/repos/{repo.slug}/pulls?state=closed&sort=updated&direction=desc"
                     f"&per_page={per_page}&page={page}", token)
        if not batch:
            break                                    # no more PRs
        page += 1
        for pr in batch:
            seen += 1
            if len(kept) >= limit:
                break
            if not pr.get("merged_at"):
                continue
            try:
                files = _get(f"{_API}/repos/{repo.slug}/pulls/{pr['number']}/files?per_page=100", token)
            except RuntimeError as e:
                print(f"  PR #{pr['number']}: {e}", flush=True)
                return kept
            fix_patch, test_patch, touched = split_files(files)
            if not fix_patch or not test_patch:           # need BOTH a code change and a test
                continue
            if not is_small_fix(fix_patch, max_files, max_code_lines):  # small/medium only
                continue
            if not added_test_funcs(test_patch):           # need at least one new test function
                continue

            problem, source = _problem_statement(repo, pr, token)
            if require_issue and not well_formed_issue(problem, source, min_issue_chars):
                continue                                    # not a validator-quality question
            inst = build_instance(repo, pr, fix_patch, test_patch, problem, source)
            d = out_dir / inst["instance_id"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "instance.json").write_text(json.dumps(inst, indent=2), encoding="utf-8")
            (d / "fix.patch").write_text(fix_patch, encoding="utf-8")
            (d / "test.patch").write_text(test_patch, encoding="utf-8")
            kept.append(inst)
            print(f"  kept {inst['instance_id']}: {inst['_code_lines']} code lines, "
                  f"F2P~{inst['FAIL_TO_PASS']}, problem={source}", flush=True)
            if require_issue:                               # surface the issue text for review
                preview = " ".join(problem.split())
                print(f"        issue: {preview[:300]}{'...' if len(preview) > 300 else ''}\n",
                      flush=True)
    return kept


def _problem_statement(repo: Repo, pr: dict, token: str | None) -> tuple[str, str]:
    """Prefer the linked issue's text (the PR body may leak the solution). Fall back to the
    PR title only if no issue is found. Always record which source was used."""
    n = linked_issue_number(pr.get("title", ""), pr.get("body", ""))
    if n:
        try:
            issue = _get(f"{_API}/repos/{repo.slug}/issues/{n}", token)
            if "pull_request" not in issue:            # it's a real issue, not another PR
                return f"{issue.get('title','')}\n\n{issue.get('body','') or ''}", f"issue#{n}"
        except Exception:  # noqa: BLE001
            pass
    return pr.get("title", ""), "pr_title_only"        # title only -> minimise solution leakage


def _main() -> None:
    ap = argparse.ArgumentParser(prog="harvest_repo")
    ap.add_argument("repo", choices=[k for k, r in REGISTRY.items() if r.owner])
    ap.add_argument("--limit", type=int, default=15, help="how many candidates to keep")
    ap.add_argument("--scan", type=int, default=60, help="how many recent PRs to scan")
    ap.add_argument("--max-code-lines", type=int, default=200)
    ap.add_argument("--max-files", type=int, default=2,
                    help="max non-test .go files the fix may touch (localized = small/medium)")
    ap.add_argument("--require-issue", action="store_true",
                    help="keep ONLY PRs backed by a substantial linked issue (validator-quality)")
    ap.add_argument("--min-issue-chars", type=int, default=120,
                    help="minimum issue text length when --require-issue is set")
    ap.add_argument("--out", type=str, default=str(ROOT / "eval" / "tasks"))
    ap.add_argument("--token", type=str, default=None, help="GitHub token (or env GITHUB_TOKEN)")
    args = ap.parse_args()

    import os
    repo = REGISTRY[args.repo]
    token = args.token or os.getenv("GITHUB_TOKEN")
    print(f"harvesting {repo.slug}: scan<= {args.scan} PRs, keep<= {args.limit}, "
          f"code<= {args.max_code_lines} lines, files<= {args.max_files}"
          f"{', issue-linked only' if args.require_issue else ''}  "
          f"(token: {'yes' if token else 'no, 60/hr'})")
    kept = harvest(repo, limit=args.limit, max_code_lines=args.max_code_lines,
                   token=token, out_dir=Path(args.out), scan=args.scan, max_files=args.max_files,
                   require_issue=args.require_issue, min_issue_chars=args.min_issue_chars)
    print(f"\n{len(kept)} candidate(s) written to {args.out}/{repo.key}-* "
          f"(all marked _needs_validation -- run the gold-gate next)")


if __name__ == "__main__":
    _main()
