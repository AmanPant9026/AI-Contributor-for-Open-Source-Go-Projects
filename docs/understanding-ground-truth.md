# Understanding Stage 1 — Ground Truth (plain-language guide)

This document exists because the ground-truth stage went by too fast. No code
here, no jargon without explanation. Read it top to bottom; by the end the files
in `eval/tasks/` should feel obvious, not mysterious.

---

## 1. The big picture: we are building an answer key

We are eventually building a program (the "agent") that takes a bug report and
writes a code fix. But how will we ever know if its fix is any *good*?

The same way a teacher grades an exam: with an **answer key**. Before the exam,
the teacher already knows the correct answers. Then they compare each student's
answer to the key and score it.

**Ground truth is our answer key.** It is a small set of bugs that *real
developers have already fixed*, where we know:

- what the bug was (the report),
- what the correct fix looked like (the real code change they merged),
- a test that fails when the bug is present and passes when it's fixed.

Once we have that, we can later run our agent on the *same* bugs and ask: did its
fix make the failing test pass? Did it touch the same files the human did? That
comparison is the whole point — and it mirrors exactly how the assignment says
*we* will be graded.

> **Key idea:** we build the answer key *first*, before the agent exists. If we
> built the agent first, we'd have no honest way to measure it.

---

## 2. Where do these bugs come from? (the "why did we fetch these" question)

The target project is **`go-playground/validator`** — a popular open-source Go
library. Like every project on GitHub, its history is full of **Pull Requests
(PRs)**.

A quick vocabulary pass, because the rest depends on it:

- **Pull Request (PR):** a proposed code change someone submitted. When the
  maintainers accept it, it gets **merged** into the project. A *merged* PR is a
  change that actually shipped — it's real, accepted, battle-tested.
- **Issue:** a bug report. Someone writes "X is broken, here's how to reproduce
  it." Often an issue is later closed by a PR that fixes it.
- **Commit:** a single saved snapshot of the code, identified by a long
  hash like `2cce309b681d…`. The project's history is a chain of commits.
- **Diff / patch:** the *difference* between two snapshots — the exact lines
  added and removed. A patch is just that difference written to a file so it can
  be re-applied elsewhere. Lines starting with `+` are added, `-` are removed.

So our raw material is: **bugs that were reported as issues and fixed by merged
PRs.** Those are gold because the fix is known-correct (humans reviewed and
merged it).

We deliberately picked **five** such bugs, chosen to be *different shapes* so our
agent gets tested on variety rather than five copies of the same easy case:

| id | what was broken | shape of the fix | files touched |
|----|-----------------|------------------|---------------|
| 1314 | postcode validation always failed | one missing line | `baked_in.go` |
| 1476 | phone numbers starting with `+0` wrongly accepted | a regex tweak | `regexes.go` |
| 1444 | `file://` URLs wrongly accepted as valid | parsing logic | `baked_in.go` |
| 1423 | crash when validating a private struct field | engine refactor (~5 files) | `validator.go`, … |
| 1284 | map-validation errors missing their keys | new methods + output fix | `validator_instance.go` |

That spread (a one-liner, a regex, parsing, an engine crash, an output bug)
proves the agent can handle small and large, single-file and multi-file.

**How we fetched them:** GitHub has a command-line tool called `gh`. Given a PR
number, it can hand us the PR's merge commit, the exact diff, and the linked
issue's text. Our script `scripts/build_gt.sh` automates that: you give it a PR
number, it pulls everything and writes out the instance. That's all "fetching"
means here — asking GitHub for the facts about a known fix.

---

## 3. Anatomy of one instance — your real `validator-1314.json`

Each bug becomes one JSON file: the **instance**. Here is yours, field by field,
with the actual values.

```json
{
  "instance_id": "go-playground__validator-1314",
  "repo": "go-playground/validator",
  "base_commit": "2cce309b681d803db45519afc303a5d1598d3de1",
  "base_tag": "v10.24.0",
  "problem_statement": "Bug: postcode_iso3166_alpha2_field validation broken …",
  "patch": "diff --git a/baked_in.go … +\tpostcodeRegexInit.Do(initPostcodes) …",
  "test_patch": "diff --git a/zz_issue1314_repro_test.go … (a new test file) …",
  "FAIL_TO_PASS": ["TestIssue1314PostcodeIso3166Alpha2Field"],
  "PASS_TO_PASS": [],
  "go_version": "1.22",
  "issue": 1314,
  "fix_pr": 1359,
  "merge_commit": "b1111542c1b3658f1a90fd070f7fd2b4f27a3fcc"
}
```

What each field means, in order:

- **`instance_id`** — a unique name for this bug. Just a label.
- **`repo`** — which project it's from.
- **`base_commit`** — *the exact snapshot of the code where the bug is still
  present.* This is the version we start from. (`2cce309…` is the snapshot tagged
  `v10.24.0`, the release right before the fix shipped.) Think of it as "rewind
  the project to just before anyone fixed this."
- **`base_tag`** — a human-friendly name for that same snapshot (`v10.24.0`).
- **`problem_statement`** — the bug report text. **This is the only thing the
  agent will ever be allowed to see.** It describes the symptom, like a real
  user would. Everything below is hidden from the agent — it's the answer key.
- **`patch`** — *the gold fix.* The real code change that fixed the bug. For
  #1314 it's literally one added line in `baked_in.go`:
  `postcodeRegexInit.Do(initPostcodes)`. We will compare the agent's fix to this.
- **`test_patch`** — *the test that catches the bug.* A small piece of test code
  that should fail on the broken version and pass once fixed. (More on the two
  ways we get this in §4.)
- **`FAIL_TO_PASS`** — the name(s) of the test that should go from **fail →
  pass**. This is the headline check: a fix "resolves" the bug if this test,
  which failed before, now passes. For #1314 it's
  `TestIssue1314PostcodeIso3166Alpha2Field`.
- **`PASS_TO_PASS`** — tests that were already passing and must *keep* passing
  (so the fix doesn't break anything else). Empty for now; we can fill it later.
- **`go_version`, `issue`, `fix_pr`, `merge_commit`** — bookkeeping: which Go
  version, which issue/PR numbers, which commit merged the fix. Useful for
  tracing back to the source; not used in scoring.

> **The single most important sentence:** the agent only ever sees
> `problem_statement` + the code at `base_commit`. The `patch`, `test_patch`, and
> the `*_TO_PASS` lists are the hidden answer key we grade against.

---

## 4. Two flavors of instance (why some have a `.repro_test.go`)

To grade a bug we need a test that fails-when-broken and passes-when-fixed.
There are two ways we got that test, and that's the only real difference between
the five instances:

**Flavor A — the PR already shipped a good test.** (#1444, #1423, #1284)
The developer who fixed the bug also added a test for it. We just reuse their
test. That reused test lives in `validator-<id>.test.patch`, and `FAIL_TO_PASS`
is the name of their test function.

**Flavor B — the PR shipped no usable failing test, so we wrote one.**
(#1314, #1476)
Some fixes didn't come with a test that captures the bug (e.g. #1314 shipped no
test at all; #1476's test happened to pass even on the broken code, so it
couldn't catch the bug). In those cases we *author* a tiny reproduction test
ourselves, based on the bug report. That authored test lives in
`validator-<id>.repro_test.go`, and `FAIL_TO_PASS` is the name of our test.

Both flavors are equally valid — in both, the test demonstrably fails on the
broken code and passes on the fixed code (we proved that; see §6). The fix code
(`patch`) is always the real human fix; only the *test* is sometimes ours.

---

## 5. The files in `eval/tasks/` — what each one is (and which to ignore)

This is the part that felt "messy." For each bug there are up to a handful of
files. **Only the `.json` truly matters** — it contains everything. The rest are
either copies the scripts read for convenience, or scratch. Here's the full list:

| file | what it is | do you need to care? |
|------|------------|----------------------|
| `validator-<id>.json` | **the instance** — the answer key, contains all fields from §3 | **yes — this is the one** |
| `validator-<id>.fix.patch` | the gold code fix, as a standalone file (same content as the JSON's `patch`) | only the scripts read it |
| `validator-<id>.test.patch` | the gold test from the PR (Flavor A) | only the scripts read it |
| `validator-<id>.repro_test.go` | the test we authored (Flavor B: 1314, 1476) | only the scripts read it |
| `validator-<id>.tests.txt` | scratch: the test names the build script detected | ignore — git-ignored |
| `_src-<id>.json` | scratch: the raw issue/PR text we fetched | ignore — git-ignored |

So when you open `eval/tasks/` and see ~20 files, it's really **5 instances**,
each = one `.json` + a couple of helper files. The two `.txt`/`_src` kinds are
throwaway and we already told git to ignore them.

(There's mild redundancy here — the `.json` embeds the patch and test, *and* we
also keep them as loose files. That's a convenience for the shell scripts. If
you'd prefer, a later cleanup can make everything read straight from the `.json`
and delete the loose copies. Not urgent.)

---

## 6. How we *verified* each instance (what `gate-gt` actually did)

A bug instance is only trustworthy if the test really catches the bug. So for
each one, `scripts/verify_gt.sh` ran this little experiment inside the Docker
sandbox (the isolated, pinned place we built in Stage 0):

```
1. Rewind the code to base_commit (the broken version).
2. Add the test (the gold test, or our authored repro).
3. Run the test  ──►  it must FAIL.   (proof: the bug is really there)
4. Now also apply the gold fix (patch).
5. Run the test again  ──►  it must PASS.  (proof: the fix really works)
6. Clean up.
```

If step 3 had *passed* (test green on the broken code), the test wouldn't be
catching anything — so the script would reject that instance and we'd fix or
drop it. That actually happened with #1476's original test, which is exactly why
we wrote our own repro for it.

When all five did fail-then-pass, we "locked" that known-good state with a git
tag called `gate-gt`. (A git tag is just a bookmark on the project's history you
can always jump back to.)

That's the entire meaning of "the dev set is grounded": five bugs, each with a
test proven to fail before the fix and pass after — a real, checkable answer key.

---

## 7. The folder layout — why it's arranged this way

Here's the top level and what each folder is *for*:

```
go-issue-agent/
├── eval/                 ← everything about GRADING (the "ruler")
│   ├── tasks/            ←   the ground-truth instances (the answer key)   ← you are here
│   └── results/          ←   scores get written here later
├── scripts/              ← runnable helpers (build_gt.sh, verify_gt.sh, check_env.sh …)
├── src/go_issue_agent/   ← the actual agent program (mostly empty for now; built in later stages)
├── config/               ← per-project settings
├── prompts/              ← the instructions we'll give the LLM (later stages)
├── docs/                 ← write-ups like this one
├── Dockerfile            ← defines the sandbox (pinned Go toolchain)
└── Makefile              ← shortcut commands
```

The logic: **ground truth is grading material, and `eval/` is the grading
folder, so ground truth lives under `eval/`.** The subfolder name `tasks/` comes
from SWE-bench, the standard benchmark our setup imitates (reviewers recognize
it). That's the only reason it's called `tasks` and not `ground_truth`.

> **If `tasks` is confusing, we can rename it.** I can change `eval/tasks/` to
> `eval/ground_truth/` (or `eval/instances/`) everywhere — the scripts and docs
> all updated together in one migration — so the folder name says what it holds.
> Just tell me the name you'd like. There is no downside to renaming now.

---

## 8. Try it yourself (read any instance with one command)

Nothing here requires understanding code. To see any instance in plain form:

```bash
# pretty-print the whole instance
python3 -m json.tool eval/tasks/validator-1314.json

# just the bug report the agent will see
python3 -c "import json;print(json.load(open('eval/tasks/validator-1314.json'))['problem_statement'])"

# just the gold fix
cat eval/tasks/validator-1314.fix.patch

# the list of test names that must flip fail->pass
python3 -c "import json;print(json.load(open('eval/tasks/validator-1314.json'))['FAIL_TO_PASS'])"
```

Swap `1314` for `1284`, `1444`, `1423`, or `1476` to inspect the others.

---

## 9. FAQ (the exact things that were confusing)

**"Ground truth inside `eval/tasks` doesn't make sense."**
Ground truth = answer key = grading material; `eval/` is the grading folder;
`tasks/` is the SWE-bench name for the instance set. Happy to rename it (§7).

**"Why so many files per bug?"**
There's one real file per bug — the `.json`. The rest are helper copies the
scripts read, plus two throwaway scratch files (already git-ignored). It's 5
instances, not 20 important things.

**"How is it working / what is it doing?"**
It isn't *doing* anything on its own — ground truth is just data sitting in
files. The *doing* was the one-time verification (§6) that proved each test
fails-when-broken and passes-when-fixed. After that, these files just wait to be
used as the yardstick when the agent starts producing fixes.

**"Why did we fetch these specific five?"**
They're real, already-merged, accepted fixes (so the "correct answer" is known),
and they're deliberately different shapes (§2) so the agent is tested on variety.

**"What does the agent actually get to see?"**
Only `problem_statement` + the code at `base_commit`. Everything else in the JSON
is hidden answer-key data.

---

*If any single sentence here is still fuzzy, point at it and I'll go deeper on
just that. We stay on Stage 1 until it's fully clear — no moving on without your
go-ahead.*
