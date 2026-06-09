"""Build ONE validator-style instance from a chosen (issue, PR) pair.

This is the workflow we used for the validator-5, toolized: a human picks a real, well-described
bug ISSUE and its merged fixing PR; this assembles the instance.json the same shape as validator's
-- issue text becomes the problem statement (no fix leakage), the PR's diff becomes the gold fix +
gold test -- ready for the Docker gold-gate. Curation stays human judgment; only the tedious
assembly (base commit, diff split, JSON) is automated. Nothing is scraped: it builds exactly the
pair you name.

Needs network to api.github.com (use a GITHUB_TOKEN for 5000/hr) -- run it on a machine with quota.

    python eval/make_instance.py gin --issue 4438 --pr 4439
    python eval/run_eval.py --validate --prefix gin     # confirm it really goes FAIL->PASS
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from repos import REGISTRY, Repo                                   # noqa: E402
from harvest_repo import (_API, _get, split_files, added_test_funcs,  # noqa: E402
                          diff_code_lines, code_files_in_diff)


def fetch_issue_text(repo: Repo, n: int, token: str | None) -> str:
    """The issue's title + body = the problem statement. Refuse if #n is actually a PR (its body
    would describe the solution and leak the fix)."""
    issue = _get(f"{_API}/repos/{repo.slug}/issues/{n}", token)
    if "pull_request" in issue:
        raise SystemExit(f"#{n} is a pull request, not an issue -- pass the ISSUE number to --issue")
    return f"{issue.get('title', '')}\n\n{issue.get('body', '') or ''}".strip()


def build_from_pair(repo: Repo, issue_n: int, pr_n: int, token: str | None, out_dir: Path) -> dict:
    problem = fetch_issue_text(repo, issue_n, token)
    pr = _get(f"{_API}/repos/{repo.slug}/pulls/{pr_n}", token)
    files = _get(f"{_API}/repos/{repo.slug}/pulls/{pr_n}/files?per_page=100", token)
    fix_patch, test_patch, touched = split_files(files)
    if not fix_patch:
        raise SystemExit(f"PR #{pr_n} changes no non-test .go file -- nothing to fix")
    if not test_patch or not added_test_funcs(test_patch):
        raise SystemExit(f"PR #{pr_n} adds no test function -- without a gold test we cannot gauge it")

    inst = {
        "instance_id": f"{repo.key}-{issue_n}",
        "repo": repo.slug,
        "base_commit": pr["base"]["sha"],
        "problem_statement": problem,           # the ISSUE text -- no solution leakage
        "patch": fix_patch,                     # gold code fix (the eval harness reads this)
        "test_patch": test_patch,               # gold test
        "FAIL_TO_PASS": added_test_funcs(test_patch),   # provisional; confirmed by the gold-gate
        "PASS_TO_PASS": [],
        "_source": "curated",                   # hand-picked, not scraped
        "_issue": issue_n,
        "_pr": pr_n,
        "_issue_url": f"https://github.com/{repo.slug}/issues/{issue_n}",
        "_pr_url": pr.get("html_url", ""),
        "_code_lines": diff_code_lines(fix_patch),
        "_code_files": code_files_in_diff(fix_patch),
        "_needs_validation": True,
    }
    d = out_dir / inst["instance_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "instance.json").write_text(json.dumps(inst, indent=2), encoding="utf-8")
    (d / "fix.patch").write_text(fix_patch, encoding="utf-8")
    (d / "test.patch").write_text(test_patch, encoding="utf-8")
    return inst


def _main() -> None:
    ap = argparse.ArgumentParser(prog="make_instance")
    ap.add_argument("repo", choices=[k for k, r in REGISTRY.items() if r.owner])
    ap.add_argument("--issue", type=int, required=True, help="the bug ISSUE number (problem statement)")
    ap.add_argument("--pr", type=int, required=True, help="the merged PR that fixed it (gold fix + test)")
    ap.add_argument("--out", type=str, default=str(ROOT / "eval" / "tasks"))
    ap.add_argument("--token", type=str, default=None, help="GitHub token (or env GITHUB_TOKEN)")
    args = ap.parse_args()

    repo = REGISTRY[args.repo]
    token = args.token or os.getenv("GITHUB_TOKEN")
    inst = build_from_pair(repo, args.issue, args.pr, token, Path(args.out))
    print(f"built {inst['instance_id']}: {inst['_code_files']} "
          f"({inst['_code_lines']} code lines), F2P={inst['FAIL_TO_PASS']}, "
          f"base={inst['base_commit'][:10]}")
    print(f"  issue: {inst['_issue_url']}")
    print(f"  pr:    {inst['_pr_url']}")
    print(f"  next:  python eval/run_eval.py --validate --prefix {repo.key}")


if __name__ == "__main__":
    _main()
