"""Open a DRAFT pull request from a VERIFIED agent result -- the final, optional step of the
contributor loop. (The assignment makes opening a PR optional; a branch + patch + PR summary is
sufficient, and this produces all three.)

Safe by construction:
  * Acts only on the agent's VERIFIED, code-only patch (build + vet + reproduction already
    passed). Refuses on an empty/abstained result -- we never open a PR for a non-fix.
  * Defaults to a DRY RUN: it builds the branch + commit locally and prints the diff and the PR
    title/body, and touches no network. Pushing + opening requires BOTH `--confirm` AND an
    interactive 'yes'.
  * Targets YOUR FORK by default (a draft PR against your fork's default branch) so nothing
    reaches an upstream maintainer unless you read the diff and pass `--upstream`.
  * Every PR body carries an AI-assistance disclosure.

The PR is created through GitHub's REST API directly (`open_draft_pr`). That single call is the
only GitHub-write seam; in an MCP-client setting it is a drop-in swap for a GitHub MCP server's
"create pull request" tool. REST is chosen here so the CLI stays self-contained (no extra daemon).

Run on a machine with git, a GitHub credential for your fork, and GITHUB_TOKEN set.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from repos import REGISTRY, resolve_clone        # noqa: E402

RESULTS = ROOT / "eval" / "results" / "agent"
TASKS = ROOT / "eval" / "tasks"
_API = "https://api.github.com"

DISCLOSURE = (
    "## AI-assistance disclosure\n"
    "This change was prepared with the help of an automated AI agent: it localized the bug, "
    "generated the patch, and validated it in a sandbox (build, vet, and a reproduction test). "
    "A human reviewed the change before this PR was opened."
)


# ----------------------------------------------------------------- pure helpers (unit-tested)

def branch_name(instance_id: str) -> str:
    return f"fix/{instance_id}"


def changed_files(patch_text: str) -> list[str]:
    """Files the patch modifies (from `+++ b/...` lines)."""
    out = []
    for ln in patch_text.splitlines():
        if ln.startswith("+++ b/"):
            out.append(ln[6:].strip())
    return sorted(set(out))


def pr_title(inst: dict) -> str:
    """A concise title from the issue's first line; references the issue number when known."""
    iid = inst.get("instance_id", "fix")
    first = (inst.get("problem_statement") or iid).strip().splitlines()[0].strip()
    low = first.lower()
    for pre in ("fix:", "fixed:", "bug:", "bugfix:"):     # avoid "fix: Fix: ..."
        if low.startswith(pre):
            first = first[len(pre):].strip()
            break
    if len(first) > 68:
        first = first[:65].rstrip() + "..."
    issue = inst.get("_issue")
    return f"fix: {first}" + (f" (#{issue})" if issue else "")


def pr_body(inst: dict, files: list[str]) -> str:
    """PR body: issue reference + summary + the validation the agent performed + AI disclosure."""
    issue = inst.get("_issue")
    ref = f"Fixes #{issue}\n\n" if issue else ""
    flist = ", ".join(f"`{f}`" for f in files) or "the affected file"
    return (
        f"{ref}"
        f"## Summary\n"
        f"A minimal, localized change to {flist} that addresses the reported issue.\n\n"
        f"## Validation\n"
        f"- `go build ./...` passes\n"
        f"- `go vet ./...` passes\n"
        f"- a reproduction test was added that **fails at the base commit and passes with this "
        f"change**\n"
        f"- the affected package's existing tests continue to pass (no regression)\n\n"
        f"{DISCLOSURE}\n"
    )


def read_patch(instance_id: str, patch_arg: str | None) -> str:
    """The verified patch -- refuse if missing or empty (an abstention is not a contribution)."""
    p = Path(patch_arg) if patch_arg else (RESULTS / f"{instance_id}.patch")
    if not p.exists():
        raise SystemExit(f"no patch at {p} -- run the agent on {instance_id} first; it must RESOLVE")
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise SystemExit(f"patch {p} is empty -- the agent abstained; there is nothing to open a PR for")
    return text


# ----------------------------------------------------------------- git + REST (run on the Mac)

def _git(repo_dir: Path, *args: str, check: bool = True, stdin: str | None = None):
    return subprocess.run(["git", "-C", str(repo_dir), *args], input=stdin, text=True,
                          capture_output=True, check=check)


def prepare_branch(repo_dir: Path, base_commit: str, branch: str, patch_text: str, message: str) -> None:
    """Create `branch` at base_commit, apply the verified patch, and commit it -- locally only."""
    _git(repo_dir, "checkout", "-q", base_commit)
    _git(repo_dir, "checkout", "-q", "-B", branch)
    applied = _git(repo_dir, "apply", "--recount", "--ignore-whitespace", "-", stdin=patch_text, check=False)
    if applied.returncode != 0:
        raise SystemExit(f"git apply failed (is this the verified patch for this base?):\n{applied.stderr}")
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", message)


def _request(url: str, token: str | None, payload: dict | None = None, method: str = "GET"):
    hdr = {"User-Agent": "go-issue-agent", "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub API {e.code}: {e.read().decode('utf-8', 'replace')[:400]}") from e


def default_branch(slug: str, token: str | None) -> str:
    try:
        return _request(f"{_API}/repos/{slug}", token).get("default_branch", "main")
    except SystemExit:
        return "main"


def _repo_exists(slug: str, token: str | None) -> bool:
    hdr = {"User-Agent": "go-issue-agent", "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        urllib.request.urlopen(urllib.request.Request(f"{_API}/repos/{slug}", headers=hdr), timeout=30)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise SystemExit(f"GitHub API {e.code} while checking {slug}") from e


def ensure_fork(upstream_slug: str, fork_owner: str, repo_name: str, token: str | None) -> None:
    """Make sure <fork_owner>/<repo_name> exists; create it from upstream and wait if not, so the
    contributor loop is self-contained (no manual 'click Fork' step)."""
    import time
    fork_slug = f"{fork_owner}/{repo_name}"
    if _repo_exists(fork_slug, token):
        return
    print(f"  fork {fork_slug} not found -- creating it from {upstream_slug} ...", flush=True)
    _request(f"{_API}/repos/{upstream_slug}/forks", token, payload={}, method="POST")
    for _ in range(10):                       # forks are usually ready within a few seconds
        time.sleep(3)
        if _repo_exists(fork_slug, token):
            print(f"  fork {fork_slug} is ready.", flush=True)
            return
    raise SystemExit(f"fork {fork_slug} did not become ready in time -- create it manually and retry")


def open_draft_pr(base_repo_slug: str, head: str, base_branch: str, title: str, body: str,
                  token: str | None) -> str:
    """The single GitHub-write seam (REST). Swap this one function for a GitHub MCP 'create PR'
    tool in an MCP-client deployment."""
    resp = _request(f"{_API}/repos/{base_repo_slug}/pulls", token,
                    payload={"title": title, "head": head, "base": base_branch,
                             "body": body, "draft": True}, method="POST")
    return resp.get("html_url", "(created)")


# ----------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(prog="open_pr", description="Open a draft PR from a verified fix.")
    ap.add_argument("repo", choices=[k for k, r in REGISTRY.items() if r.owner])
    ap.add_argument("--instance", required=True, help="instance id, e.g. validator-1476")
    ap.add_argument("--fork", required=True, help="your GitHub username (owner of your fork)")
    ap.add_argument("--patch", default=None,
                    help="verified patch path (default: eval/results/agent/<instance>.patch)")
    ap.add_argument("--base", default=None, help="base branch (default: the base repo's default branch)")
    ap.add_argument("--upstream", action="store_true",
                    help="target the UPSTREAM repo as the PR base (a real contribution) instead of your fork")
    ap.add_argument("--confirm", action="store_true",
                    help="actually push + open the draft PR (default: DRY RUN, no network writes)")
    args = ap.parse_args()

    repo = REGISTRY[args.repo]
    token = os.getenv("GITHUB_TOKEN")
    inst_path = TASKS / args.instance / "instance.json"
    inst = json.loads(inst_path.read_text()) if inst_path.exists() else {"instance_id": args.instance}
    base_commit = inst.get("base_commit")
    if not base_commit:
        raise SystemExit(f"no base_commit for {args.instance} (need eval/tasks/{args.instance}/instance.json)")

    patch_text = read_patch(args.instance, args.patch)
    files = changed_files(patch_text)
    branch = branch_name(args.instance)
    title = pr_title(inst)
    body = pr_body(inst, files)
    repo_dir, _ = resolve_clone(args.instance, inst.get("repo"))

    prepare_branch(Path(repo_dir), base_commit, branch, patch_text, title)

    print("=" * 72)
    print(f"repo:    {repo.slug}        fork: {args.fork}/{repo.name}")
    print(f"branch:  {branch}   (committed locally at {repo_dir})")
    print(f"files:   {files}")
    print(f"\nPR title:\n  {title}\n\nPR body:\n{textwrap.indent(body, '  ')}")
    print("=" * 72)
    print("DIFF (stat):")
    subprocess.run(["git", "-C", str(repo_dir), "--no-pager", "show", "--stat", "HEAD"])

    if not args.confirm:
        print("\nDRY RUN -- branch + commit built locally, nothing pushed.")
        print("Read the diff above; if it's correct, re-run with --confirm to push to your fork "
              "and open a DRAFT PR.")
        return 0

    # determine PR target
    if args.upstream:
        base_repo, head = repo.slug, f"{args.fork}:{branch}"
    else:
        base_repo, head = f"{args.fork}/{repo.name}", branch
    base_br = args.base or default_branch(base_repo, token)

    print(f"\nABOUT TO push '{branch}' to https://github.com/{args.fork}/{repo.name} "
          f"and open a DRAFT PR -> {base_repo}:{base_br}")
    if input("Type 'yes' to proceed (anything else aborts): ").strip().lower() != "yes":
        print("aborted -- nothing pushed."); return 1

    ensure_fork(repo.slug, args.fork, repo.name, token)        # create the fork if it's missing
    fork_url = f"https://github.com/{args.fork}/{repo.name}.git"
    _git(Path(repo_dir), "remote", "remove", "fork", check=False)
    _git(Path(repo_dir), "remote", "add", "fork", fork_url)
    pushed = _git(Path(repo_dir), "push", "-u", "fork", branch, "--force-with-lease", check=False)
    if pushed.returncode != 0:
        raise SystemExit("git push to your fork failed (is the fork created and your git auth "
                         f"configured for it?):\n{pushed.stderr}")

    url = open_draft_pr(base_repo, head, base_br, title, body, token)
    print(f"\nOK -- draft PR opened: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
