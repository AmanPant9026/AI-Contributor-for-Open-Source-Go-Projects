# Stage 1 — Ground Truth: Complete Reference

> **Status:** complete and locked at git tag `gate-gt`.
> **One line:** we built and *verified* an answer key — five real, already-fixed
> bugs from `go-playground/validator`, each proven to fail on the broken code and
> pass on the fixed code — so that every later claim about the agent can be
> measured honestly.

This document is the full, detailed account of Stage 1: what it is, why it
exists, every file, every script line that matters, and how it was verified.
It assumes no prior knowledge — terms are defined as they appear.

---

## Table of contents

1. [Why Stage 1 exists (the what & why)](#1-why-stage-1-exists)
2. [Vocabulary you need](#2-vocabulary)
3. [The dev set — the five bugs](#3-the-dev-set)
4. [The instance schema (the JSON, field by field)](#4-the-instance-schema)
5. [Two flavors of instance (and the repro_test.go question)](#5-two-flavors)
6. [The folder layout](#6-the-folder-layout)
7. [Every file type, explained with real code](#7-every-file-type)
8. [Reading a diff (the patch format)](#8-reading-a-diff)
9. [How we fetched instances — `build_gt.sh` line by line](#9-build_gt)
10. [How we verified instances — `verify_gt.sh` line by line](#10-verify_gt)
11. [The gate (`gate-gt`) — definition and results](#11-the-gate)
12. [Environment this stage depends on](#12-environment)
13. [Gotchas we hit and how we fixed them](#13-gotchas)
14. [Inspect / re-run it yourself](#14-inspect)
15. [What Stage 1 unlocks](#15-unlocks)

---

## 1. Why Stage 1 exists

We are building an **agent**: a program that reads a bug report and writes a code
fix. The hard question is *how will we ever know if its fix is good?*

The answer is the same one a teacher uses: an **answer key**. Before grading, you
must already know the correct answers. **Ground truth is that answer key.**

| Concept | In this project |
|---|---|
| The exam questions | the bug reports (`problem_statement`) the agent will read |
| The correct answers | the real human fixes (`fix.patch`) developers already merged |
| The grading rubric | a test that fails when the bug is present, passes when fixed |
| Building the key *before* the exam | we build & verify ground truth *before* writing any agent |

**Why first?** If we built the agent before the answer key, we'd have no
objective way to score it — we'd be guessing. By building the ruler first, every
future statement ("the agent resolved N of 5 bugs") is checkable. This is
test-driven development applied to a whole system.

---

## 2. Vocabulary

| Term | Plain meaning |
|---|---|
| **Repository (repo)** | a project's code + full history. Ours: `go-playground/validator`. |
| **Commit** | one saved snapshot of the code, named by a long hash like `2cce309b…`. History is a chain of commits. |
| **Tag** | a human-friendly bookmark on a commit, e.g. `v10.24.0`, or our `gate-gt`. |
| **Issue** | a bug report filed by a user ("X is broken"). |
| **Pull Request (PR)** | a proposed code change. When accepted it is **merged**. A *merged* PR is a change that actually shipped. |
| **Merge commit** | the commit that records a PR being merged. Its **first parent** (`^1`) is the code *just before* the PR. |
| **Diff / patch** | the difference between two snapshots: lines added (`+`) and removed (`-`). A `.patch` file stores that so it can be re-applied. |
| **Checkout** | switching the working copy of the repo to a specific commit ("rewind to this point"). |
| **Sandbox** | the pinned Docker container (built in Stage 0) where Go code is compiled/tested in isolation. |
| **FAIL_TO_PASS** | the test(s) that must go from *failing* (bug present) to *passing* (bug fixed). The headline grade. |
| **PASS_TO_PASS** | tests that were already passing and must *stay* passing (no regressions). Empty for now. |
| **Instance** | one bug packaged as data: report + fix + test + metadata. One `instance.json`. |

---

## 3. The dev set

Five real, confirmed-merged bug fixes, chosen to be deliberately *different
shapes* so the agent is tested on variety, not five copies of one easy case.

| id | bug (symptom) | fix shape | file(s) changed | test origin | base commit (broken version) |
|----|---------------|-----------|-----------------|-------------|-------------------------------|
| **1314** | valid postcodes always rejected | one missing init line | `baked_in.go` | **we wrote it** (PR had none) | `2cce309…` (`v10.24.0`) |
| **1476** | phone `+0…` wrongly accepted | regex tightened | `regexes.go` | **we wrote it** (PR's test too weak) | `a221118…` |
| **1444** | `file://` wrongly accepted as URL | parsing logic | `baked_in.go` | reused PR's test | `3fd4678…` |
| **1423** | crash on private struct field | engine refactor (~5 files) | `validator.go`, … | reused PR's tests | `f9a5a1f…` |
| **1284** | map-validation errors miss their keys | new methods + wiring | `validator_instance.go` | reused PR's tests | `94e89f0…` |

**Coverage achieved:** five shapes (missing-init, regex boundary, parsing, engine
panic, output correctness), three areas of the codebase (validator registry,
traversal engine, helpers), both test origins, and one genuine multi-file fix
(#1423). That spread is what makes the dev set a fair yardstick.

---

## 4. The instance schema

Each bug becomes one `instance.json`. Here is the contract, with the real #1314
values.

| field | what it is | who sees it | #1314 value |
|---|---|---|---|
| `instance_id` | unique label | — | `go-playground__validator-1314` |
| `repo` | source project | — | `go-playground/validator` |
| `base_commit` | snapshot **where the bug still exists** (the starting point) | agent (checks out here) | `2cce309b681d…` |
| `problem_statement` | the bug report text | **agent (the only field it sees)** | "Bug: postcode_iso3166… broken in v10.21.0 …" |
| `patch` | the gold **code fix** (no test files) | hidden (answer key) | the one-line `baked_in.go` diff |
| `test_patch` | the gold **test** as a diff | hidden | a new-file diff for the repro |
| `FAIL_TO_PASS` | test name(s) that must flip fail→pass | hidden | `["TestIssue1314PostcodeIso3166Alpha2Field"]` |
| `PASS_TO_PASS` | tests that must stay green | hidden | `[]` (none recorded yet) |
| `go_version` | Go toolchain to use | harness | `"1.24"` |
| `issue` / `fix_pr` / `merge_commit` | provenance (trace back to GitHub) | — | `1314` / `1359` / `b111154…` |

> **The single most important rule:** the agent is given **only**
> `problem_statement` + the repo at `base_commit`. Everything else (`patch`,
> `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`) is the hidden key we grade against.
> Leaking any of it to the agent would be like handing a student the answer sheet.

The `instance.json` is **self-contained** — it embeds copies of the patch and
test text inside it. That's why the JSON alone is enough; the loose `fix.patch` /
`test.patch` / `repro_test.go` files next to it are conveniences the shell
scripts read directly.

---

## 5. Two flavors

Every bug needs a test that **fails when broken, passes when fixed**. There are
two ways we obtained that test — and it's the *only* structural difference
between the five instances.

| Flavor | When used | Test lives in | `FAIL_TO_PASS` comes from | Your bugs |
|---|---|---|---|---|
| **A — reuse the PR's test** | the developer shipped a test that already catches the bug | `test.patch` | the test function name in that patch | 1444, 1423, 1284 |
| **B — author our own test** | the PR shipped no test, or one that *doesn't* catch the bug | `repro_test.go` | the function name in that file | 1314, 1476 |

**This answers "why do some have `repro_test.go` and some don't":**

- **`repro_test.go` present** → we wrote the test ourselves (Flavor B).
- **`repro_test.go` absent** → we reused the developer's test, which is in
  `test.patch` (Flavor A).

Why each Flavor-B bug needed an authored test:

| bug | reason the PR's own test was unusable |
|---|---|
| **1314** | the fix PR (#1359) shipped **no test at all** |
| **1476** | the PR's `TestE164` **passed even on the broken code** — it never tried a `+0…` number, so it couldn't detect the bug. Our `repro_test.go` feeds exactly `"+0123456789"` and asserts rejection. |

Note **1476 has both** files: the weak `test.patch` (kept for reference) and our
`repro_test.go`. The verifier always prefers `repro_test.go` when present.

---

## 6. The folder layout

After grouping, each bug is self-contained in its own folder:

```
go-issue-agent/
├── eval/                         ← everything about GRADING (the "ruler")
│   ├── tasks/                    ←   the ground-truth instances (the answer key)
│   │   ├── validator-1284/       ←     one folder per bug
│   │   │   ├── instance.json     ←       the answer key (self-contained)
│   │   │   ├── fix.patch         ←       the gold code fix
│   │   │   ├── test.patch        ←       the gold test (Flavor A)
│   │   │   ├── tests.txt         ←       scratch (git-ignored)
│   │   │   └── src.json          ←       scratch (git-ignored)
│   │   ├── validator-1314/  {instance.json, fix.patch, repro_test.go}   ← Flavor B
│   │   ├── validator-1423/  {instance.json, fix.patch, test.patch, …}
│   │   ├── validator-1444/  {instance.json, fix.patch, test.patch, …}
│   │   └── validator-1476/  {instance.json, fix.patch, repro_test.go, test.patch, …}
│   └── results/                  ←   scores written here (Stage 2 onward)
├── scripts/                      ← build_gt.sh, verify_gt.sh, migrate_tasks.sh, check_env.sh …
├── src/go_issue_agent/           ← the agent program (built in later stages)
├── docs/                         ← this document and the others
├── Dockerfile / Makefile         ← the pinned sandbox + shortcuts
└── config/ prompts/              ← settings & LLM prompts (later stages)
```

**Why ground truth sits under `eval/`:** it is grading material, and `eval/` is
the grading area. The subfolder name `tasks/` follows the SWE-bench benchmark
convention (reviewers recognize it).

---

## 7. Every file type

Leaving aside `instance.json` (covered in §4), here is every other file.

| file | format | role | how it's produced | committed to git? |
|---|---|---|---|---|
| `fix.patch` | unified diff | **the cure** — the real code fix | split from the PR by `build_gt.sh` | yes |
| `test.patch` | unified diff | **the thermometer (Flavor A)** — the PR's gold test | split from the PR by `build_gt.sh` | yes |
| `repro_test.go` | full Go file | **the thermometer (Flavor B)** — the test we authored | written by hand | yes |
| `tests.txt` | plain text | scratch: the test names the build script detected | `build_gt.sh` | no (git-ignored) |
| `src.json` | JSON | scratch: the raw issue/PR text before cleanup | `build_gt.sh` (from `gh`) | no (git-ignored) |

### 7.1 `fix.patch` — the gold code fix

This is the **answer**: the exact change the human made. Example, the *entire*
`validator-1314/fix.patch`:

```diff
diff --git a/baked_in.go b/baked_in.go
index 2f66c1836..dab60f18f 100644
--- a/baked_in.go
+++ b/baked_in.go
@@ -1417,6 +1417,7 @@ func isPostcodeByIso3166Alpha2Field(fl FieldLevel) bool {
 		panic(fmt.Sprintf("Bad field type %T", currentField.Interface()))
 	}
 
+	postcodeRegexInit.Do(initPostcodes)
 	reg, found := postCodeRegexDict[currentField.String()]
 	if !found {
 		return false
```

Reading it (full diff primer in §8): only the line marked `+` is new. The
surrounding unmarked lines are *context* so the tool knows where to insert. In
English: "inside `isPostcodeByIso3166Alpha2Field` in `baked_in.go`, add the line
`postcodeRegexInit.Do(initPostcodes)`." That one line lazily builds the postcode
regex table that the v10.21.0 refactor had stopped building — which is why every
postcode was failing. **No test code lives here**, only the fix.

### 7.2 `test.patch` — the reused gold test (Flavor A)

Same diff format, but it edits a `*_test.go` file. Example, `validator-1444`'s
test patch — the developer changed the expected result for `file://` URLs:

```diff
@@ -8255,7 +8255,9 @@ func TestUrl(t *testing.T) {
 		{"file://localhost/c:/WINDOWS/file.txt", true},
-		{"file://", true},
+		{"file:", false},
+		{"file:/", false},
+		{"file://", false},
 		{"file:////remotehost/path/file.txt", true},
```

In English: in the existing `TestUrl` table, the row `{"file://", true}`
(expected *valid*) became `{"file://", false}` (expected *invalid*), and two new
rows were added. So on the **broken** code, `TestUrl` still thinks `file://` is
valid → the new expectation fails → the test fails. With the fix, `file://` is
rejected → the test passes. That fail→pass is what makes it a usable thermometer.
Here `FAIL_TO_PASS = ["TestUrl"]` (the existing function the patch modifies).

### 7.3 `repro_test.go` — the authored test (Flavor B)

A complete, runnable Go test file we wrote. The *entire* `validator-1314/repro_test.go`:

```go
package validator

import "testing"

// Reproduction for go-playground/validator issue #1314:
// postcode_iso3166_alpha2_field validation was broken in v10.21.0 because the
// postcode regexes were never lazily initialised. A valid US postcode should
// pass; before the fix (PR #1359) it fails.
func TestIssue1314PostcodeIso3166Alpha2Field(t *testing.T) {
	type Example struct {
		PostCode    string `validate:"required,postcode_iso3166_alpha2_field=CountryCode"`
		CountryCode string `validate:"required,iso3166_1_alpha2"`
	}

	validate := New(WithRequiredStructEnabled())

	ex := Example{CountryCode: "US", PostCode: "12345"}
	if err := validate.Struct(ex); err != nil {
		t.Fatalf("expected valid US postcode to pass, got: %v", err)
	}
}
```

Line-by-line:

| line(s) | what it does |
|---|---|
| `package validator` | makes the file part of the validator package, so it can call `New` directly (a "white-box" test, matching the project's existing tests) |
| `import "testing"` | Go's standard test library — no external dependency added |
| `// …` comments | explain *why* the test exists (provenance: issue #1314, PR #1359) |
| `func TestIssue1314…(t *testing.T)` | a Go test: any function named `Test…` taking `*testing.T` is run by `go test` |
| `type Example struct { … }` | rebuilds the exact struct from the bug report, with the `validate:"…"` tags that trigger the broken rule |
| `validate := New(WithRequiredStructEnabled())` | creates a validator the same way the bug reporter did |
| `validate.Struct(ex)` with `"12345"` | validates a **valid** US postcode |
| `if err != nil { t.Fatalf(…) }` | **the assertion**: a valid postcode must pass. On broken code it errors → test fails; with the fix it passes |

The 1476 repro is the mirror image — it asserts an **invalid** input is rejected:

```go
func TestPR1476E164RejectsLeadingZero(t *testing.T) {
	validate := New()
	if err := validate.Var("+0123456789", "e164"); err == nil {
		t.Fatalf("expected +0123456789 to be rejected by e164, but it was accepted")
	}
	if err := validate.Var("+12025550123", "e164"); err != nil {
		t.Fatalf("expected +12025550123 to be valid e164, got: %v", err)
	}
}
```

`err == nil` means "no error" = accepted. On the broken regex `+0…` is accepted,
so `err == nil` is true → `t.Fatalf` fires → test fails. With the fix, `+0…` is
rejected (`err != nil`), so the first check passes; the second line confirms a
genuinely valid number still works.

### 7.4 `tests.txt` and `src.json` — scratch

| file | what's inside | why it exists | safe to ignore? |
|---|---|---|---|
| `tests.txt` | the test function names the build script extracted (e.g. `TestUrl`) | a stepping-stone the script writes, then copies into the JSON's `FAIL_TO_PASS` | yes — git-ignored |
| `src.json` | the raw `{title, body}` `gh` fetched from the issue/PR | kept so we can re-check the original wording before it was cleaned into `problem_statement` | yes — git-ignored |

---

## 8. Reading a diff

Every `.patch` file is a **unified diff**. The markers:

| line starts with | meaning |
|---|---|
| `diff --git a/X b/X` | a new file section begins, for file `X` |
| `index abc..def` | the before/after blob hashes (ignore for reading) |
| `--- a/X` / `+++ b/X` | the "before" and "after" filrenames (`/dev/null` = file didn't exist) |
| `@@ -L,n +L,m @@ context` | a **hunk** header: starts at line `L`, the trailing text is the enclosing function |
| ` ` (space) | **context** line — unchanged, shown so the tool can locate the edit |
| `+` | a line **added** |
| `-` | a line **removed** |

When we "apply" a patch we replay those edits onto the code. We apply tolerantly
(`git apply --ignore-whitespace`, falling back to `patch --fuzz=3`) so small
line-number drift doesn't break it.

---

## 9. `build_gt.sh` — fetching an instance

Run as `bash scripts/build_gt.sh <pr_number>`. It turns a merged PR into an
instance folder, with **no copy-paste of giant diffs**. Step by step:

| step | code (abridged) | what / why |
|---|---|---|
| find the merge commit | `MC=$(gh pr view "$PR" … --jq '.mergeCommit.oid')` | ask GitHub for the commit that merged this PR; if none, it isn't merged → stop |
| compute the base | `BASE=$(git rev-parse "${MC}^1")` | `^1` = the merge's first parent = the code **just before** the fix = the broken version |
| split out the **code** fix | `git diff "${MC}^1" "$MC" -- ':(exclude)*_test.go' > fix.patch` | the PR's changes to non-test files = the gold fix |
| split out the **test** | `git diff "${MC}^1" "$MC" -- '*_test.go' > test.patch` | the PR's changes to test files = the gold test |
| find test names | `grep '^\+func Test' …` (else `grep '^@@.*func Test'`) | the headline `FAIL_TO_PASS`: prefer tests the PR *added*; if none, the existing test functions it *modified* (read from the `@@` hunk headers) |
| get the bug report | `gh issue view "$ISSUE"` (else the PR body) | prefer the **linked issue** text for `problem_statement` (a user's words), since a PR body can leak the fix |
| assemble the JSON | a small Python block | write everything above into `instance.json`, with `FAIL_TO_PASS` filled in |

Two subtleties worth knowing:

- The `… || true` after the `grep`s is deliberate: under `set -e` a `grep` that
  finds nothing returns non-zero and would abort the whole script. `|| true`
  lets "no match" be a normal outcome (which is how Flavor-A "modified, not
  added" tests are handled by the fallback line).
- `go_version` is written as `"1.24"` because recent validator commits require
  Go ≥ 1.24 (see §12).

---

## 10. `verify_gt.sh` — proving an instance is trustworthy

Run as `bash scripts/verify_gt.sh <id>`. This is the heart of the stage: it runs
a small experiment to prove the bug is real and the fix works. Pseudocode:

```
1. read base_commit from instance.json
2. pick test mode:
     if repro_test.go exists  -> MODE=repro, test names read FROM that file
     else                     -> MODE=patch, test names read from FAIL_TO_PASS
3. reset the repo to base_commit            (the broken version)
4. install the test (copy repro_test.go, or apply test.patch)
5. run the test  --> it MUST FAIL           (proof the bug is real)
6. apply fix.patch                          (the gold code fix)
7. run the test again --> it MUST PASS      (proof the fix works)
8. reset the repo (clean up)
```

The key lines and why they're written that way:

| code | what / why |
|---|---|
| `IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"` | use the pinned Go-1.24 sandbox image from Stage 0 |
| `RE="^($(grep -oE 'func Test…' "$REPRO" …))\$"` | when a repro file exists, derive the exact test name from it (so the JSON can't drift out of sync) |
| `sandbox(){ docker run --rm -v "$REPO":/workspace … bash -c 'export PATH=…; '"$1"; }` | run a command inside the throwaway container against the mounted checkout. `bash -c` (not `-lc`) so `PATH` keeps `go` on it |
| `reset_base(){ git checkout --force; git reset --hard; git clean -fdq; }` | wipe the working copy back to a pristine broken state between phases |
| `apply(){ git apply --recount --ignore-whitespace … \|\| patch -p1 --fuzz=3 … }` | apply a patch tolerantly so minor line drift doesn't fail |
| `if sandbox "go test -run '$RE' ./..." ; then … fail "PASSED at base"` | the **must-fail-at-base** check: if the test *passes* on broken code, it isn't capturing the bug → reject the instance |
| `sandbox "go test …" \|\| fail "FAILED with fix"` | the **must-pass-with-fix** check |

Note step 5's logic: a "fail at base" can be a real assertion failure (1314,
1476, 1444), a **panic** (1423 — the bug crashes), or even a **compile error**
(1284 — the test calls a method the fix introduces). All three are valid
"fails," because in every case the test does not pass on the broken code.

---

## 11. The gate (`gate-gt`)

**Definition.** For every instance: the test **fails** at `base_commit` (bug
real) and **passes** after `fix.patch` (fix works). An instance that can't be
made to fail at base is not capturing its bug and is dropped.

**Result (all five green):**

| id | mode | "fail at base" was a… | result |
|----|------|------------------------|--------|
| 1314 | repro | assertion failure (postcode rejected) | PASSED |
| 1476 | repro | assertion failure (`+0…` accepted) | PASSED |
| 1444 | patch | assertion failure (`file://` accepted) | PASSED |
| 1423 | patch | **panic** (crash on private field) | PASSED |
| 1284 | patch | **compile error** (`VarWithKey` undefined yet) | PASSED |

When all five passed, the state was locked with the git tag **`gate-gt`** — a
bookmark you can always return to (`git reset --hard gate-gt`). That tag is the
meaning of "the dev set is grounded."

---

## 12. Environment this stage depends on

| dependency | value | why |
|---|---|---|
| sandbox image | `go-issue-agent-sandbox:dev` | the pinned container from Stage 0 |
| Go toolchain | **1.24** | recent validator commits set `go >= 1.24.0` in `go.mod`; the image refuses older. Older code (e.g. #1314's `v10.24.0`) still builds on 1.24 (backward compatible). |
| `gh` (GitHub CLI) | authenticated | how `build_gt.sh` fetches PR/issue facts |
| Docker | running | the sandbox boundary |
| module cache | `.cache/repos/validator` | a single local clone reused across verifications |

---

## 13. Gotchas we hit and how we fixed them

Real reproducibility notes specific to Stage 1.

| symptom | root cause | fix |
|---|---|---|
| `go.mod requires go >= 1.24.0 (running 1.22.5; GOTOOLCHAIN=local)` | recent validator bases need Go 1.24; the image had 1.22 | bumped the sandbox base to `golang:1.24`, tag → `:dev` |
| 1444 & 1423 had empty `FAIL_TO_PASS` | their PRs **modified** existing tests rather than adding new `func Test…`; the name-grep only caught added funcs | added a fallback that reads the function from the `@@` hunk header |
| `build_gt.sh` silently aborted before writing JSON | under `set -e`, a `grep` with no match returns non-zero and kills the script | added `\|\| true` so "no match" is allowed |
| 1314 verified vacuously (`[no tests to run]`) | the generalized verifier didn't load the authored `repro_test.go` | made the verifier prefer a `repro_test.go` and auto-detect its test name |
| 1476 "passed at base" | the PR's `TestE164` didn't exercise `+0…`, so it couldn't catch the bug | authored a targeted `repro_test.go` for the `+0…` case |

---

## 14. Inspect / re-run it yourself

| goal | command |
|---|---|
| pretty-print an instance | `python3 -m json.tool eval/tasks/validator-1314/instance.json` |
| see only the bug report (what the agent sees) | `python3 -c "import json;print(json.load(open('eval/tasks/validator-1314/instance.json'))['problem_statement'])"` |
| see the gold fix | `cat eval/tasks/validator-1314/fix.patch` |
| see a reused gold test | `cat eval/tasks/validator-1444/test.patch` |
| see an authored test | `cat eval/tasks/validator-1314/repro_test.go` |
| re-verify one bug | `bash scripts/verify_gt.sh 1314` |
| re-verify all five (quiet) | `for id in 1314 1284 1476 1444 1423; do printf "%-6s " "$id:"; bash scripts/verify_gt.sh "$id" 2>&1 \| tail -1; done` |

Swap `1314` for any of `1284 1444 1423 1476`.

---

## 15. What Stage 1 unlocks

- **An objective definition of "correct."** We can now score *any* patch (the
  agent's, later) by whether it flips the failing test to passing on the same
  base — the headline grader axis.
- **A reusable instrument.** The same fail→pass mechanism becomes the Stage 2
  eval harness (it scores any candidate, not just the gold one).
- **A safety net.** `gate-gt` is a known-good, revertible checkpoint; the scratch
  files are git-ignored so the answer key stays clean.

**Next stage (only when you say so): Stage 2 — the eval harness**, which wraps
this fail→pass check in code (`run_eval.py` + `metrics.py`) so it scores patches
and emits the metrics table automatically.

---

*If any single table row or code block here is still unclear, point at it and
I'll expand just that. We remain on Stage 1 until you're satisfied.*
