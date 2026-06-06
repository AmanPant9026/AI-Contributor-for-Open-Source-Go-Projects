# Stage 1 — Ground Truth: The Complete, Detailed Reference

> **Status:** complete and locked at git tag `gate-gt`.
> **One-line summary:** before writing any agent, we built and *proved correct* an
> answer key of five real, already-fixed bugs from `go-playground/validator` —
> each with the bug report, the real fix, a test that flips fail→pass, and a
> verified set of ~250 regression tests that must stay green — so that every
> future claim about the agent can be measured objectively.

This is the long-form reference. It explains every concept from first
principles, dissects the real files and scripts on your disk line by line, works
through concrete micro-examples (a diff character by character, a regex symbol by
symbol, the commit graph drawn out, real command output), and answers *what*,
*why*, and *how* at each step. If you read it top to bottom you should understand
not just *what* each file is but *why it has to exist* and *how the machinery
actually runs*.

It supersedes the earlier version of this document: it adds the full treatment of
the **`PASS_TO_PASS` regression guard** and the scripts that derive, verify,
audit, and probe it (`build_ptp.sh`, the extended `verify_gt.sh`, `audit_gt.sh`,
`probe_ptp.py`).

---

## Table of contents

1. [The problem Stage 1 solves, from scratch](#1-the-problem)
2. [Core vocabulary, explained properly](#2-vocabulary)
3. [How Go testing actually works (including `-v` output)](#3-go-testing)
4. [How git history works, and why `^1` is the broken version](#4-git-history)
5. [How to read a unified diff (worked character by character)](#5-diffs)
6. [The five bugs — real root-cause walkthroughs](#6-the-five-bugs)
7. [The instance schema (every JSON field, in depth)](#7-schema)
8. [The two flavors, and the `repro_test.go` question answered fully](#8-flavors)
9. [The folder layout and every file](#9-files)
10. [`build_gt.sh` — the fetcher, line by line](#10-build_gt)
11. [`build_ptp.sh` — deriving the regression guard, in depth](#11-build_ptp)
12. [`verify_gt.sh` — the 3-step prover, line by line](#12-verify_gt)
13. [`audit_gt.sh` — the paranoid re-check, in depth](#13-audit)
14. [`probe_ptp.py` — proving the guard is *enforced*, in depth](#14-probe)
15. [The Docker sandbox command, dissected](#15-docker)
16. [The gate (`gate-gt`) and what each bug proved](#16-gate)
17. [Environment, gotchas, and reproducibility notes](#17-env)
18. [Inspect and re-run it yourself](#18-inspect)
19. [What Stage 1 unlocks, and the road ahead](#19-unlocks)

---

<a name="1-the-problem"></a>
## 1. The problem Stage 1 solves, from scratch

The end goal of the whole project is an **agent**: a program that takes a GitHub
issue (a bug report written by a human) and produces a *code fix* for it. That's
the headline deliverable.

Now here is the uncomfortable question that governs everything: **how would we
ever know whether the agent's fix is good?** A fix can *look* plausible and be
completely wrong. It can compile and still not solve the bug. It can solve the
bug but break ten other things. "It looks right" is not a measurement.

Think about how a teacher grades an exam. The teacher does not read each answer
and form an opinion on the spot. The teacher prepares an **answer key in
advance** — the correct answers, settled before any student sits the exam — and
then mechanically compares each student's answer to that key. The key is what
makes grading *objective* instead of a matter of taste.

**Ground truth is our answer key.** It is a small, carefully chosen set of bugs
for which we already know, with certainty:

1. what the bug was (the report a user would file),
2. what the correct fix looks like (because a real developer already fixed it and
   the maintainers merged that fix),
3. a concrete test that *fails* while the bug is present and *passes* once it is
   fixed — a thermometer that objectively reads "sick" or "healthy," and
4. **which already-passing tests must remain passing** — so a "fix" that secretly
   breaks something else cannot sneak through.

Point 4 is the part this revision adds in full. A fix is not good merely because
it makes the broken test pass; it must *also* not regress the rest of the code.
That second half is the `PASS_TO_PASS` regression guard, and §11–§14 are devoted
to building, verifying, auditing, and proving it.

Once we possess all four, grading the agent becomes mechanical and honest: run the
agent on the *same* bug, take whatever code change it produces, and ask the
thermometer — does the failing test now pass, *and* do all the regression tests
still pass? Did the agent change the same files the human did? This is exactly,
deliberately, how the assignment says *we* will be graded (compare the agent's
output to accepted PRs on a set of axes), so our ruler mirrors the grader's ruler.

> **The crucial ordering:** we build and verify the answer key **before** we
> write a single line of the agent. If we did it the other way around, we would
> have nothing trustworthy to measure the agent against — we'd be grading by
> vibes. Building the ruler first is "test-driven development" applied to an
> entire system: write down what "correct" means, prove the definition works by
> checking it against a known-correct answer, *then* build the thing being
> measured.

Stage 1 is the construction and proof of that answer key. Nothing in Stage 1
"runs" on its own afterward; it is *data plus a one-time proof*. The data then
sits and waits to be used as the yardstick from Stage 2 onward.

A note on lineage so the conventions make sense: the schema and naming we use
(`instance_id`, `base_commit`, `FAIL_TO_PASS`, `PASS_TO_PASS`, a `tasks/` folder)
come from **SWE-bench**, the standard academic benchmark for exactly this task —
"can a system resolve a real GitHub issue?" Reusing its shape means our numbers
are comparable to published work and recognizable to a reviewer, and our
`PASS_TO_PASS` is derived by the same method SWE-bench uses.

---

<a name="2-vocabulary"></a>
## 2. Core vocabulary, explained properly

These terms recur constantly. Each is explained in a sentence and then expanded.

| Term | One-line meaning |
|---|---|
| Repository (repo) | a project's complete source code together with its entire change history |
| Commit | one immutable snapshot of the whole codebase, named by a hash |
| Tag | a friendly name pinned to one commit |
| Branch | a moving pointer to the latest commit of a line of work |
| Issue | a bug report filed by a user |
| Pull Request (PR) | a proposed bundle of changes; when accepted it is *merged* |
| Merge commit | the commit that records a PR landing |
| First parent (`^1`) | for a merge commit, the state of the project just before the PR |
| Diff / patch | the precise set of line additions/removals between two snapshots |
| Checkout | switching your working files to a particular commit |
| Package (Go) | a directory of `.go` files compiled together; the unit `go test` runs |
| Sandbox | the pinned Docker container where Go is compiled and tested in isolation |
| Test | a function the Go toolchain runs to assert the code behaves correctly |
| FAIL_TO_PASS | the test(s) that must change from failing → passing (the bug's thermometer) |
| PASS_TO_PASS | tests that were already passing and must **keep** passing (the regression guard) |
| Regression | a change that breaks something that used to work |
| Liveness (of a test name) | the property that the name refers to a real test that actually runs (not a typo/ghost) |
| Instance | one bug packaged as data (report + fix + test + regression guard + metadata) |

**Repository.** Not just the current code — the *history*. Every change ever
made to `go-playground/validator` is recorded as a sequence of commits. We can
ask git to show us the project exactly as it looked at any past moment.

**Commit.** A snapshot, identified by a 40-character SHA-1 hash like
`2cce309b681d803db45519afc303a5d1598d3de1`. The hash is computed from the
content, so it is effectively a fingerprint: the same hash always means the
exact same code. We usually abbreviate to the first ~7–12 characters
(`2cce309b`). A commit also records its *parent* commit(s), which is how history
forms a chain.

**Tag.** A label humans attach to a specific commit so we don't have to memorize
hashes. `v10.24.0` is a tag the validator maintainers put on the commit they
released as version 10.24.0. We also create our own tags — `gate-0`, `gate-gt` —
as bookmarks for known-good states of *our* repo.

**Package.** This term matters more now than before, because `PASS_TO_PASS` is
*package-scoped*. In Go, a package is a directory whose `.go` files are compiled
as one unit. `validator`'s root directory is the package `validator`; its
`translations/ar` directory is a different package. `go test .` runs the tests in
the current package only; `go test ./...` runs every package. When we derive the
regression guard, we scope it to the package(s) the fix touches (for all five of
our bugs, that is the root package) so we guard where regressions would actually
appear without running thousands of unrelated tests.

**Regression / regression guard.** A *regression* is when a change breaks
something that previously worked. The *regression guard* is the list of tests we
assert must keep passing — `PASS_TO_PASS`. It is the second half of "is this fix
good": not just "did it fix the bug" (`FAIL_TO_PASS`) but "did it avoid breaking
anything" (`PASS_TO_PASS`).

**Liveness.** A subtle but important property introduced by the audit (§13). A
test *name* in `PASS_TO_PASS` is only useful if it refers to a real test that
actually runs. A misspelled name is "dead" — it silently matches nothing. Proving
every name is *live* (it really runs and passes) is what `audit_gt.sh` does.

---

<a name="3-go-testing"></a>
## 3. How Go testing actually works (including `-v` output)

Everything in Stage 1 ultimately bottoms out in running Go tests, so it's worth
understanding the mechanics precisely. This section is expanded with the verbose
output format, because `build_ptp.sh` and `audit_gt.sh` parse it.

**What is a Go test?** In Go, any function in a file whose name ends in
`_test.go`, named `TestXxx`, and taking a single argument `t *testing.T`, is a
test. The toolchain discovers and runs these automatically. Inside, you call
methods on `t` to signal failure — e.g. `t.Fatalf("message", …)` records a
failure and stops that test immediately.

```go
func TestSomething(t *testing.T) {
    got := DoTheThing()
    if got != "expected" {
        t.Fatalf("wanted 'expected', got %q", got)   // <- marks the test FAILED
    }
}
```

If the function returns without calling any failure method, the test **passed**.

**Running tests.** The command is `go test`. Key forms used in Stage 1:

| command | meaning |
|---|---|
| `go test .` | build and run every test in **the current package only** |
| `go test ./...` | build and run **every** test in **every** package (`./...` = "this module, recursively") |
| `go test -run '<regex>' ./...` | only run test functions whose **name matches** the regular expression |
| `go test -v <pkgs>` | **verbose**: print a `--- PASS:`/`--- FAIL:` line for every test (we parse this) |
| `go build ./...` | compile every package, but do **not** run tests |

**The `-run` regex.** `-run` takes a regular expression and runs only the tests
whose names match it. We always build an *anchored* pattern like
`^(TestUrl)$` — `^` means "start of name," `$` means "end," and `(A|B)` means "A
or B." So `^(TestValidate_VarWithKey|TestValidate_VarWithKeyCtx)$` runs exactly
those two functions and nothing else. The `PASS_TO_PASS` run uses the same trick
with ~250 names joined by `|`.

**Exit codes — the actual pass/fail signal.** Every command-line program returns
an integer "exit code" when it finishes: `0` conventionally means success,
non-zero means failure. `go test` exits **0** if all selected tests pass, and
**non-zero** if *any* selected test fails. Our scripts read that exit code; they
do not parse the human-readable output to decide pass/fail. This is robust: we
don't have to understand Go's printout, just whether the number was zero.

**Verbose output, and how we parse passing names.** When we need to know *which*
tests passed (to derive `PASS_TO_PASS`), exit code isn't enough — we need names.
`go test -v` prints, for each test, a line like:

```
=== RUN   TestAlpha
--- PASS: TestAlpha (0.00s)
=== RUN   TestUrl
--- FAIL: TestUrl (0.01s)
    validator_test.go:8275: Index: 41 URL failed ...
```

Top-level results start at column 0 with `--- PASS: ` or `--- FAIL: `. Subtests
are indented (`    --- PASS: TestAlpha/sub`). So to collect the **top-level
passing** test names we grep for lines that *start with* `--- PASS: ` and pull out
the `TestXxx` token:

```bash
go test -v . | grep -E '^--- PASS: ' | sed -E 's#^--- PASS: (Test[A-Za-z0-9_]+).*#\1#' | sort -u
```

This exact pipeline is the heart of `build_ptp.sh` (§11) and `audit_gt.sh` (§13).

**A subtle trap — "[no tests to run]".** If the `-run` regex matches *no* test
(for example because the test file wasn't actually loaded), `go test` prints
`[no tests to run]` and exits **0** — success! This bit us once: an early version
of the verifier for bug #1314 didn't load our authored test, so the regex matched
nothing, `go test` exited 0, and the script wrongly concluded "the test passed at
base." We fixed it by making the verifier explicitly install the test first. The
lesson baked into the current scripts: a vacuous pass is still a pass to `go
test`, so you must ensure the test is genuinely present — and, for `PASS_TO_PASS`,
you must *count* how many actually ran (the blind spot `audit_gt.sh` closes; §13).

**Three ways a test can "fail at base."** When we run the bug's test on the
*broken* code, "fail" can manifest three different ways, and all three are valid:

| failure mode | what happens | which bug shows this |
|---|---|---|
| assertion failure | the test runs, an `if` check trips, `t.Fatalf` fires → non-zero exit | 1314, 1476, 1444 |
| panic (crash) | the code under test crashes; Go marks the test failed → non-zero exit | 1423 |
| compile error | the test references a symbol that doesn't exist yet → the package won't build → non-zero exit | 1284 |

In every case `go test` exits non-zero, which is all "fail at base" requires.
(The compile-error case in 1284 has a consequence for deriving `PASS_TO_PASS`,
explained in §11.)

---

<a name="4-git-history"></a>
## 4. How git history works, and why `^1` is the broken version

To build an instance we must check out the project *exactly as it was just before
the fix*. Here's how we find that point precisely.

Git history is a chain of commits, each pointing back to its parent:

```
… ─► C1 ─► C2 ─► C3 ─► …          (each arrow points from a commit to its child)
```

When a PR is merged, git records a special **merge commit** that has **two**
parents: the project's mainline just before the merge, and the PR's own work.

```
                      ┌─────────────────┐
  mainline … ─► P ────┤  M (merge commit)│ ──► … (history continues)
                      └───────▲─────────┘
                              │
              PR's commits ───┘
```

- `M` is the **merge commit** — the moment the fix landed.
- `M^1` (read "M's first parent") is `P` — the mainline **immediately before** the
  PR. That is the project *with the bug still present*, by definition: the fix is
  in the PR, and `P` is the state before the PR was merged.

So in `build_gt.sh` we compute:

```bash
BASE="$(git rev-parse "${MC}^1")"
```

`git rev-parse` turns a reference into its concrete hash; `${MC}^1` is "the first
parent of the merge commit." `BASE` is therefore the exact broken snapshot. We
record it as `base_commit` in the instance, and the verifier always starts by
checking out `BASE`.

For bug #1314 we instead used the release tag `v10.24.0` (commit `2cce309b…`) as
the base — the public release immediately *before* the fix shipped in v10.25.0.
It's the same idea (a known pre-fix snapshot) expressed with a friendly tag.

**Why this matters for patches:** because `BASE` is the precise parent of the
merge, the PR's diff was *computed against exactly this snapshot*. That means the
fix patch applies cleanly — there's no line-number drift to fight.

**Why this matters for `PASS_TO_PASS`:** the regression guard is derived by
running the package's tests *at this same `BASE`* (and at base+fix). Because the
base is a real, released-quality commit, its own test suite genuinely passes
there — which is what lets us trust "passing at base" as a baseline of stable
tests (see §11).

---

<a name="5-diffs"></a>
## 5. How to read a unified diff (worked character by character)

A `.patch` file is a **unified diff** — a compact recipe of edits. Understanding
it is essential because `fix.patch` and `test.patch` are both diffs, and because
`build_ptp.sh` reads `fix.patch` to discover which packages the fix touches.

Here is a tiny synthetic diff; we'll read every line:

```diff
diff --git a/greeter.go b/greeter.go
index 1a2b3c4..5d6e7f8 100644
--- a/greeter.go
+++ b/greeter.go
@@ -3,5 +3,6 @@ func Greet(name string) string {
 	if name == "" {
 		name = "world"
 	}
-	return "Hi " + name
+	greeting := "Hello, "
+	return greeting + name
 }
```

| line | marker | meaning |
|---|---|---|
| `diff --git a/greeter.go b/greeter.go` | header | a section of changes to `greeter.go` begins (`a/` = before, `b/` = after) |
| `index 1a2b3c4..5d6e7f8 100644` | header | the file's content fingerprint before/after and its file mode; ignore when reading |
| `--- a/greeter.go` | header | the "before" file (`/dev/null` here would mean the file didn't exist before — a brand-new file) |
| `+++ b/greeter.go` | header | the "after" file — **this line is what `build_ptp.sh` scans to learn which files (and thus packages) the fix touches** |
| `@@ -3,5 +3,6 @@ func Greet…` | hunk header | this *hunk* covers 5 lines starting at line 3 in the old file, 6 lines starting at line 3 in the new file; the trailing text names the enclosing function |
| ` 	if name == "" {` | space prefix | **context** — unchanged, shown so the tool can locate the edit precisely |
| `-	return "Hi " + name` | `-` prefix | this line is **removed** |
| `+	greeting := "Hello, "` | `+` prefix | this line is **added** |
| `+	return greeting + name` | `+` prefix | this line is **added** |

So the recipe reads: "in `greeter.go`, around line 3, delete `return "Hi " +
name` and insert the two new lines, keeping the surrounding `if` block as it is."
A line with neither `+` nor `-` is just there for *positioning* — it isn't
changed.

**Deriving the touched package from the diff.** `build_ptp.sh` takes every
`+++ b/<path>` line, keeps the `.go` ones, and reduces each to its directory:

```
+++ b/baked_in.go            → dirname → "."            → package "."
+++ b/translations/ar/ar.go  → dirname → translations/ar→ package "./translations/ar"
```

For all five of our bugs the changed files live at the repo root, so the touched
package is `.` and the regression guard runs `go test .` on the root package.

**Applying a patch** means replaying this recipe onto a real file. We apply
tolerantly:

```bash
git apply --recount --ignore-whitespace "$PATCH"  ||  patch -p1 --fuzz=3 < "$PATCH"
```

- `git apply` is git's patch applier. `--ignore-whitespace` lets tabs/spaces
  differ slightly without rejecting; `--recount` recomputes the line counts in
  case they're a touch off.
- If git apply still refuses, we fall back to the classic Unix `patch` tool with
  `--fuzz=3`, which allows the context lines to be off by up to 3 lines and still
  find the right spot. Belt and suspenders, so minor drift never blocks us.

---

<a name="6-the-five-bugs"></a>
## 6. The five bugs — real root-cause walkthroughs

Understanding the actual bugs makes every file concrete. Each entry: the symptom,
the root cause, the fix, how the test catches it, and the verified size of its
regression guard.

| id | one-line bug | fix shape | flavor | FAIL_TO_PASS | PASS_TO_PASS |
|---|---|---|---|---|---|
| 1314 | valid postcodes wrongly rejected | 1 line, `baked_in.go` | B (authored) | 1 | 242 |
| 1476 | e164 accepts a leading-zero number | regex, `regexes.go` | B (authored) | 1 | 271 |
| 1444 | `file://` wrongly accepted as URL | ~39 lines, `baked_in.go` | A (reused) | 1 (`TestUrl`) | 263 |
| 1423 | panic on unexported struct field | ~538 lines, 4 files | A (reused) | 2 | 243 |
| 1284 | map errors miss their keys | ~78 lines, `validator_instance.go` | A (reused) | 3 | 265 |

### 6.1 Bug 1314 — postcodes always rejected

- **Symptom.** Validating a valid US postcode like `"12345"` with the
  `postcode_iso3166_alpha2_field` rule fails, when it should pass. Reported
  against v10.22.0.
- **Root cause.** Internally, the postcode regular-expression table
  (`postCodeRegexDict`) is built *lazily* — only on first use — via a one-time
  initializer: `postcodeRegexInit.Do(initPostcodes)`. (`sync.Once.Do` runs its
  argument exactly once, ever.) A refactor in PR #1270 (v10.21.0) accidentally
  deleted that initializer call from the `…_field` variant of the function.
  Result: `postCodeRegexDict` stayed **empty**, so the lookup
  `reg, found := postCodeRegexDict[country]` always set `found = false`, and the
  function returned `false` (invalid) for *every* postcode. The sibling function
  `isPostcodeByIso3166Alpha2` still had the initializer — that surviving sibling
  is the localization "tell."
- **Fix (the entire `fix.patch`).** Re-add the one missing line:
  ```diff
  @@ -1417,6 +1417,7 @@ func isPostcodeByIso3166Alpha2Field(fl FieldLevel) bool {
   		panic(fmt.Sprintf("Bad field type %T", currentField.Interface()))
   	}
   
  +	postcodeRegexInit.Do(initPostcodes)
   	reg, found := postCodeRegexDict[currentField.String()]
   	if !found {
   		return false
  ```
- **How the test catches it.** Our authored `repro_test.go` validates `"12345"`
  and asserts no error. On broken code the dict is empty → error → `t.Fatalf` →
  test fails. With the fix the dict is populated → no error → test passes.
- **Regression guard.** 242 other root-package tests pass at base and at base+fix;
  the one-line fix changes none of them, so all 242 are recorded as
  `PASS_TO_PASS`.

### 6.2 Bug 1476 — phone numbers starting with `+0` wrongly accepted

- **Symptom.** The `e164` rule (international phone format) accepts
  `"+0123456789"`. Real E.164 country codes never start with `0`, so this is
  invalid and should be rejected.
- **Root cause — read the regex symbol by symbol.** The old pattern was:
  ```
  ^\+[1-9]?[0-9]{7,14}$
  ```
  | token | matches |
  |---|---|
  | `^` | start of string |
  | `\+` | a literal `+` |
  | `[1-9]?` | **optionally** one digit 1–9 (the `?` makes it optional) |
  | `[0-9]{7,14}` | between 7 and 14 of any digit 0–9 |
  | `$` | end of string |

  Trace `"+0123456789"`: `\+` eats `+`; `[1-9]?` matches **zero** characters
  (it's optional); `[0-9]{7,14}` then happily eats `0123456789` (ten digits) →
  the whole thing **matches** → accepted. That's the bug: because the leading
  digit was optional, a number could start with `0`.
- **Fix (the entire `fix.patch`, in `regexes.go`).**
  ```diff
  -	e164RegexString = "^\\+[1-9]?[0-9]{7,14}$"
  +	e164RegexString = "^\\+?[1-9]\\d{1,14}$"
  ```
  The new pattern `^\+?[1-9]\d{1,14}$` makes the `+` optional (`\+?`) but the
  **first digit mandatory and 1–9** (`[1-9]`), followed by 1–14 more digits
  (`\d{1,14}`). Now trace `"+0123456789"`: `\+?` eats `+`; `[1-9]` must match the
  next char `0` → **fails** (0 isn't in 1–9) → no match → rejected. Fixed.
- **Why we authored a test instead of reusing the PR's.** The PR shipped a test
  `TestE164`, but its cases all happen to give the *same* verdict under both the
  old and new regex — so `TestE164` passes even on the broken code and can't
  detect the bug. Our `repro_test.go` targets the one input that distinguishes
  them, `"+0123456789"`, and asserts it's rejected.
- **Regression guard.** 271 tests pass both ways and are recorded. Note that
  `TestE164` itself *is* one of them (it passes under both regexes), so the fix's
  regression guard correctly includes it — the new regex must not break the
  PR's own (weak) test either.

### 6.3 Bug 1444 — `file://` wrongly accepted as a URL

- **Symptom.** The `url` rule treats `"file://"` (and `"file:"`, `"file:/"`) as
  valid, when those are not usable URLs.
- **Root cause.** The `isURL` logic accepted these degenerate `file:`-scheme
  strings.
- **Fix.** A ~39-line tightening of `isURL` in `baked_in.go` so those forms are
  rejected.
- **How the test catches it (the reused `test.patch`).** The PR modified the
  existing `TestUrl` table-driven test, flipping the expected result for
  `file://` from `true` (valid) to `false` (invalid) and adding two more
  negatives:
  ```diff
  @@ -8255,7 +8255,9 @@ func TestUrl(t *testing.T) {
   		{"file://localhost/c:/WINDOWS/file.txt", true},
  -		{"file://", true},
  +		{"file:", false},
  +		{"file:/", false},
  +		{"file://", false},
   		{"file:////remotehost/path/file.txt", true},
  ```
  On broken code, `file://` is still accepted, so the new expectation
  (`false`) doesn't match reality → `TestUrl` fails. With the fix it's rejected →
  matches → passes. `FAIL_TO_PASS = ["TestUrl"]` (the existing function the patch
  edits — which is why our extractor reads the function name from the `@@` hunk
  header rather than from an added `func`).
- **Regression guard.** 263 tests pass both ways. `TestUrl` is *excluded* (it's
  the `FAIL_TO_PASS` test) — at base it fails, so it can't be a "pass-to-pass."

### 6.4 Bug 1423 — crash when validating a private struct field

- **Symptom.** With private-field validation enabled, validating a struct that
  has an unexported (lowercase) field *crashes the program* instead of returning
  a normal validation error.
- **Root cause.** The engine read field values with `field.Interface()`. Go's
  reflection refuses to hand out the value of an *unexported* field via
  `.Interface()` — it **panics** with "reflect: reflect.Value.Interface: cannot
  return value obtained from unexported field or method." So any unexported field
  reached this line and crashed.
- **Fix.** A larger refactor (~538 lines across 4 files including `validator.go`,
  `struct_level.go`, `util.go`, `baked_in.go`) replacing `field.Interface()` with
  a new helper `getValue()` that reads unexported fields safely. This is our
  deliberately *big, multi-file* instance, proving the agent (later) and the
  harness handle more than one-liners.
- **How the test catches it (reused `test.patch`).** The PR added cases to
  `TestPrivateFieldsStruct` using private map/pointer fields. On broken code the
  first such case hits `field.Interface()` and **panics** → the test fails by
  crash. With the fix, `getValue()` reads them safely → the test returns the
  expected errors → passes. (`TestImageValidation` is also in `FAIL_TO_PASS`
  because the patch touches it; both must go fail→pass.)
- **Regression guard.** 243 tests pass both ways. The 2 `FAIL_TO_PASS` tests are
  excluded; everything else passing in the root package (245 at base, minus the 2)
  is the guard.

### 6.5 Bug 1284 — map-validation errors miss their keys

- **Symptom.** When validating a map, the returned errors don't carry the map
  *key* that failed, so you can't tell which entry was bad.
- **Root cause.** There was no public method to validate a single value *with* an
  associated key, so `ValidateMapCtx` couldn't attach keys to its errors.
- **Fix.** A ~78-line addition to `validator_instance.go` introducing
  `VarWithKey` / `VarWithKeyCtx` and wiring `ValidateMapCtx` to use them.
- **How the test catches it (reused `test.patch`).** The PR's tests call the
  new methods `VarWithKey` / `VarWithKeyCtx`. On broken code those methods
  **don't exist yet**, so the test file *fails to compile* → `go test` exits
  non-zero → "fails at base." With the fix the methods exist, it compiles, and
  the tests pass. `FAIL_TO_PASS` is the three new test functions.
- **Regression guard.** 265 tests. This bug is the reason `build_ptp.sh` runs its
  *baseline* without the gold test installed: with the test patch present, the
  base wouldn't even compile (it references `VarWithKey`), so we could never read
  a baseline of passing tests. Running the baseline *without* the test patch keeps
  the base compilable and yields the true set of already-passing tests (§11).

---

<a name="7-schema"></a>
## 7. The instance schema (every JSON field, in depth)

Each bug is one `instance.json`. Below is every field with the real #1314 value
and a full explanation of its role.

| field | example (#1314) | role — in depth |
|---|---|---|
| `instance_id` | `go-playground__validator-1314` | a globally-unique label, in SWE-bench's `owner__repo-number` style. Used to name results and group artifacts. Purely an identifier. |
| `repo` | `go-playground/validator` | which project this bug is from. Lets the harness know what to clone. |
| `base_commit` | `2cce309b681d…` | the snapshot **where the bug still exists** (§4). The verifier and (later) the agent both start here. The most operationally important field. |
| `problem_statement` | "Bug: postcode… broken in v10.21.0 …" | the bug report. **The one and only field the agent is allowed to read.** It must describe the *symptom* like a user would, never the fix. |
| `patch` | the one-line `baked_in.go` diff | the gold **code fix** (no test files). The thing the agent's fix is compared against. Hidden from the agent. |
| `test_patch` | a new-file diff adding the repro | the gold **test**, stored as a diff so it can be applied. Hidden. |
| `FAIL_TO_PASS` | `["TestIssue1314PostcodeIso3166Alpha2Field"]` | the **headline grade**: the test name(s) that must go fail→pass. Hidden. |
| `PASS_TO_PASS` | a list of 242 test names | tests that were already green and must **stay** green (the regression guard). Populated by `scripts/build_ptp.sh` (§11). Hidden. |
| `go_version` | `"1.24"` | which Go toolchain to use (recent validator needs ≥1.24, §17). |
| `issue` | `1314` | the GitHub issue number (provenance). |
| `fix_pr` | `1359` | the PR that fixed it (provenance). |
| `merge_commit` | `b111154…` | the commit that merged the fix (provenance). |

**What `PASS_TO_PASS` actually looks like.** It is not a placeholder anymore — it
is a long, concrete list. For #1444, for example, the field begins:

```json
"PASS_TO_PASS": [
  "TestASCIIValidation", "TestAbilityToValidateNils", "TestAddFunctions",
  "TestAliasTags", "TestAlpha", "TestAlphaNumeric", "TestAlphaUnicodeValidation",
  "TestAlphanumericUnicodeValidation",  … 255 more …
]
```

263 names in total for that instance. The list is sorted and de-duplicated, and
none of its entries appear in `FAIL_TO_PASS`.

> **Restate the golden rule, because it's the soul of the design:** the agent
> sees **only** `problem_statement` + the code at `base_commit`. The `patch`,
> `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS` are the hidden answer key.
> Showing any of them to the agent would be cheating — like grading a student
> who was handed the answer sheet.

**Self-containment.** `instance.json` embeds full copies of the patch and test
text inside it (as the `patch` and `test_patch` strings). So the JSON *alone* is
a complete instance. The loose `fix.patch` / `test.patch` / `repro_test.go` files
next to it hold the same content as standalone files, purely because the shell
scripts find it convenient to `git apply` a file rather than a JSON string. (The
audit, §13, checks that the embedded `patch` is byte-for-byte equal to the loose
`fix.patch`, so the two can never silently drift.)

---

<a name="8-flavors"></a>
## 8. The two flavors, and the `repro_test.go` question answered fully

Every bug needs a **thermometer**: a test that reads "sick" on the broken code
and "healthy" on the fixed code. We obtained that thermometer in one of two ways,
and **this is the only structural difference between the five instances.**

| Flavor | When we use it | Where the test lives | `FAIL_TO_PASS` source | Bugs |
|---|---|---|---|---|
| **A — reuse the developer's test** | the PR shipped a test that genuinely catches the bug | `test.patch` | the test-function name in that patch | 1444, 1423, 1284 |
| **B — author our own test** | the PR shipped *no* test, or one that *doesn't* catch the bug | `repro_test.go` | the function name in that file | 1314, 1476 |

**Directly answering "why do some files have `repro_test.go` and some don't":**

- If you see a **`repro_test.go`** in a bug's folder, it means **we wrote that
  bug's test by hand** (Flavor B). The Go file *is* the thermometer.
- If you see **no `repro_test.go`** (only a `test.patch`), it means **we reused
  the developer's own test** (Flavor A). The thermometer is inside `test.patch`.

The reasons the two Flavor-B bugs needed hand-written tests:

| bug | why the PR's own test was unusable → we authored one |
|---|---|
| **1314** | the fix PR (#1359) shipped **no test at all**. There was simply nothing to reuse, so we wrote `repro_test.go` from the bug report's code sample. |
| **1476** | the PR *did* ship `TestE164`, but every one of its cases gives the same verdict on broken and fixed code (it never tries a `+0…` number), so it can't detect the bug. We wrote `repro_test.go` to feed exactly `"+0123456789"` and assert rejection. |

Both flavors are equally valid — in *both*, the test demonstrably fails on broken
code and passes on fixed code (§16 shows the proof). And in both, the *fix*
(`fix.patch`) is always the **real human fix**; only the *test* is sometimes
ours. Bug 1476 happens to carry **both** a `test.patch` (the weak reused one,
kept for reference) and a `repro_test.go` (the one we actually use). The verifier
always prefers `repro_test.go` when present.

**How the flavor interacts with `PASS_TO_PASS` derivation.** When `build_ptp.sh`
runs the "base+fix" pass, it installs whichever thermometer the instance uses
(repro file or test patch) before applying the fix — exactly as the verifier
does — so the derived guard reflects the real fixed-and-tested package. The
*baseline* pass, however, installs **no** thermometer (see §11 for why).

---

<a name="9-files"></a>
## 9. The folder layout and every file

After grouping, each bug is self-contained in its own folder:

```
eval/tasks/
├── validator-1284/   instance.json  fix.patch  test.patch   (+ scratch: tests.txt, src.json)
├── validator-1314/   instance.json  fix.patch  repro_test.go
├── validator-1423/   instance.json  fix.patch  test.patch   (+ scratch)
├── validator-1444/   instance.json  fix.patch  test.patch   (+ scratch)
└── validator-1476/   instance.json  fix.patch  repro_test.go  test.patch  (+ scratch)
```

Why under `eval/`: ground truth is grading material, and `eval/` is the grading
area of the project. The subfolder `tasks/` follows the SWE-bench convention.

Every file type, with full detail:

| file | format | role | produced by | in git? |
|---|---|---|---|---|
| `instance.json` | JSON | **the instance** — the self-contained answer key (§7), now including the populated `PASS_TO_PASS` | `build_gt.sh` + `build_ptp.sh` (or by hand for 1314) | yes |
| `fix.patch` | unified diff | **the cure** — the real code fix (no tests) | `build_gt.sh` splits it from the PR | yes |
| `test.patch` | unified diff | **the thermometer, Flavor A** — the PR's own test | `build_gt.sh` splits it from the PR | yes |
| `repro_test.go` | full Go file | **the thermometer, Flavor B** — the test we wrote | written by hand | yes |
| `tests.txt` | plain text | scratch: detected test-function names | `build_gt.sh` | no (git-ignored) |
| `src.json` | JSON | scratch: raw issue/PR text from `gh` before cleanup | `build_gt.sh` | no (git-ignored) |
| `.before/.after/.ftp/.ptp.tmp` | plain text | scratch: passing-test name lists during guard derivation | `build_ptp.sh` | no (deleted at end of run) |

**The scratch files in detail:**

- `tests.txt` — when `build_gt.sh` reads a `test.patch`, it greps out the test
  function names and writes them here, then copies them into `FAIL_TO_PASS`.
  Git-ignored; the script's sticky note to itself.
- `src.json` — the raw `{"title": …, "body": …}` JSON that `gh` returned before we
  trimmed it into `problem_statement`. Kept only to re-check wording. Git-ignored.
- the four `.tmp` files — `build_ptp.sh`'s working scratch: tests passing at base
  (`before`), at base+fix (`after`), the `FAIL_TO_PASS` names to exclude (`ftp`),
  and the resulting intersection (`ptp`). Deleted at the end of one derivation.

So when the folder looks busy, remember: **one `instance.json` is the real
artifact; `fix.patch`/`test.patch`/`repro_test.go` are convenience copies of
content already inside the JSON; everything else is a disposable note.**

---

<a name="10-build_gt"></a>
## 10. `build_gt.sh` — the fetcher, line by line

This script turns a merged PR number into a complete instance folder, fetching
everything from GitHub so no giant diffs are ever pasted by hand. Run it as
`bash scripts/build_gt.sh <pr_number>`.

```bash
set -euo pipefail
```
Safety switches: `-e` abort on any unhandled error, `-u` error on use of an unset
variable, `-o pipefail` make a pipeline fail if *any* stage fails (not just the
last). These catch mistakes early.

```bash
PR="${1:?usage: build_gt.sh <pr_number> [id]}"
ID="${2:-$PR}"
SLUG="go-playground/validator"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
DIR="$ROOT/eval/tasks/validator-$ID"; mkdir -p "$DIR"
```
`PR` is the first argument (the `${1:?…}` form prints the usage message and exits
if it's missing). `ID` defaults to the PR number. `SLUG` is the GitHub repo.
`ROOT` resolves the repo root relative to the script's own location. `REPO` is the
local clone of validator. `DIR` is this bug's output folder.

```bash
MC="$(gh pr view "$PR" --repo "$SLUG" --json mergeCommit --jq '.mergeCommit.oid')"
[ -n "$MC" ] || { echo "no merge commit (is #$PR MERGED?)"; exit 1; }
git -C "$REPO" fetch --all --tags --quiet
BASE="$(git -C "$REPO" rev-parse "${MC}^1")"
```
Ask GitHub for the PR's **merge commit** hash; if empty, the PR was never merged
→ stop. Then compute `BASE = MC^1`, the broken pre-fix snapshot (§4).

```bash
git -C "$REPO" diff "${MC}^1" "$MC" -- ':(exclude)*_test.go' > "$DIR/fix.patch"
git -C "$REPO" diff "${MC}^1" "$MC" -- '*_test.go'           > "$DIR/test.patch"
```
The heart of the split. `git diff BASE MC` is the PR's entire change; the
`-- <pathspec>` filters which files: everything **except** tests → `fix.patch`
(the cure); **only** tests → `test.patch` (the thermometer).

```bash
ADDED="$(grep -E '^\+func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/^\+func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true)"
if [ -n "$ADDED" ]; then printf '%s\n' "$ADDED" > "$DIR/tests.txt"
else { grep -E '^@@.*func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/.*func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true; } > "$DIR/tests.txt"; fi
```
Work out the `FAIL_TO_PASS` test names: first try `+func TestFoo` (tests the PR
*added*, e.g. 1284); if none, fall back to the `@@ … func TestBar …` hunk header
(tests the PR *modified*, e.g. 1444/1423). The trailing `|| true` is essential —
under `set -e` a `grep` with no match returns non-zero and would kill the script.

```bash
# (issue text fetch + Python assembly of instance.json with PASS_TO_PASS:[] placeholder)
```
The embedded Python writes `instance.json` with the schema of §7, initially with
`"PASS_TO_PASS": []`. That placeholder is filled in by the next script.

**In one sentence:** `build_gt.sh` asks GitHub for a merged PR, splits its diff
into a code fix and a test, figures out the `FAIL_TO_PASS` names, grabs the bug
report, and writes a complete `instance.json` — with `PASS_TO_PASS` left empty for
`build_ptp.sh` to populate.

---

<a name="11-build_ptp"></a>
## 11. `build_ptp.sh` — deriving the regression guard, in depth

This is the script that fills in `PASS_TO_PASS`. Run it once per instance:
`bash scripts/build_ptp.sh <id>`.

### 11.1 What it produces and why

**What.** It computes the list of tests that should be treated as the regression
guard for one bug, and writes that list into the bug's `instance.json`.

**Why.** `FAIL_TO_PASS` answers "did the fix make the broken thing work?"
`PASS_TO_PASS` answers the equally important opposite: "did the fix avoid breaking
things that already worked?" Without it, a candidate that deletes half the library
but happens to satisfy the one bug test would score as `resolved`. With it, such a
candidate is caught because the regression tests it broke no longer pass.

### 11.2 The exact method (and why each choice is made)

The SWE-bench-standard derivation is an **intersection of two runs**, scoped to
the package(s) the fix touches:

| step | action | why this exact choice |
|---|---|---|
| 1 | parse `fix.patch` for `+++ b/*.go` paths → unique directories → packages | scope the guard to where regressions actually appear; keeps the run to hundreds of tests, not thousands |
| 2 | check out **base**, install **nothing**, run `go test -v <pkgs>` → record passing names = `before` | the baseline of already-stable tests. **No gold test is installed here** — the key subtlety (next paragraph) |
| 3 | check out **base**, install the **gold test**, apply the **fix**, run `go test -v <pkgs>` → record passing names = `after` | the tests that pass once the fix and its test are in place |
| 4 | `PASS_TO_PASS = (before ∩ after) − FAIL_TO_PASS` | intersection ⇒ only tests stable *both* before and after; subtract the bug tests themselves |
| 5 | write the sorted, de-duplicated list into `instance.json` | it becomes part of the hidden answer key |

**Why the baseline (step 2) installs no gold test.** Consider 1284: its
`test_patch` calls `VarWithKey`, a method that does not exist at base. If we
installed that test before running the baseline, the package would *fail to
compile*, `go test` would emit no `--- PASS:` lines, and `before` would be empty —
making `PASS_TO_PASS` empty too. By running the baseline against the untouched
base, the package compiles and we get the true set of already-passing tests. The
gold test is only needed in the "after" run, where the fix has made it compile.

**Why the intersection, not just "after".** A test could pass after the fix but
have been *failing* (or flaky) at base for unrelated reasons; including it would
make the guard demand something that wasn't true to begin with. Requiring
membership in *both* sets guarantees we only enforce tests that were genuinely
stable.

**Why subtract `FAIL_TO_PASS`.** Those are the bug's own tests. At base they fail
(that's the point), so they are not "pass-to-pass"; and they are already enforced
separately as the headline grade.

### 11.3 The name-parsing pipeline

Both runs collect top-level passing names with the pipeline from §3:

```bash
go test -v <pkgs> 2>/dev/null | grep -E '^--- PASS: ' \
  | sed -E 's#^--- PASS: (Test[A-Za-z0-9_]+).*#\1#' | sort -u
```

The set algebra is then plain Unix tools on sorted files:

```bash
comm -12 before.tmp after.tmp | grep -vxF -f ftp.tmp > ptp.tmp
```
`comm -12` prints lines common to both sorted files (the intersection);
`grep -vxF -f ftp.tmp` removes any line that exactly matches a `FAIL_TO_PASS`
name (`-v` invert, `-x` whole-line, `-F` fixed-string, `-f` patterns-from-file).

### 11.4 Worked example — the real output

Running it across the five instances produced exactly:

```
== build PASS_TO_PASS for validator-1284  (packages: . ) ==
passing at base:     265
passing at base+fix: 268      ← fix ADDS 3 tests (the new VarWithKey ones)
PASS_TO_PASS:        265 tests

== build PASS_TO_PASS for validator-1314  (packages: . ) ==
passing at base:     242
passing at base+fix: 243      ← repro ADDS 1 test
PASS_TO_PASS:        242 tests

== build PASS_TO_PASS for validator-1423  (packages: . ) ==
passing at base:     245
passing at base+fix: 245
PASS_TO_PASS:        243 tests  ← 245 minus the 2 FAIL_TO_PASS tests

== build PASS_TO_PASS for validator-1444  (packages: . ) ==
passing at base:     264
passing at base+fix: 264
PASS_TO_PASS:        263 tests  ← 264 minus TestUrl

== build PASS_TO_PASS for validator-1476  (packages: . ) ==
passing at base:     271
passing at base+fix: 272      ← repro ADDS 1 test
PASS_TO_PASS:        271 tests
```

Every number is explained by the method: where the fix *adds* tests, base+fix is
larger than base; where the fix *modifies* an existing test (1444's `TestUrl`,
1423's two), that test is in the base count but is subtracted as `FAIL_TO_PASS`.
The arithmetic matching the method exactly is itself a sanity check.

### 11.5 Why package-scoped, not whole-module

We could run `go test -v ./...` and guard *every* package. We deliberately don't:
it would multiply the test count (and runtime) several-fold, drag in
environment-sensitive tests from unrelated packages, and add little — a fix to the
root package almost never regresses `translations/zh`. Package-scoping is the
SWE-bench-Go norm and the right rigor/speed balance.

---

<a name="12-verify_gt"></a>
## 12. `verify_gt.sh` — the 3-step prover, line by line

This is the *proof* that an instance is trustworthy, and it is the script that
`gate-gt` runs. Since the regression guard was added, it has **three** steps, not
two. Run as `bash scripts/verify_gt.sh <id>`. The experiment:

```
reset to base → add the test → run it (must FAIL) → apply the fix
              → run the bug test (must PASS) → run PASS_TO_PASS (must PASS) → clean up
```

```bash
set -uo pipefail
ID="${1:?usage: verify_gt.sh <id e.g. 1284>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/.cache/repos/validator"
IMG="${SANDBOX_IMAGE:-go-issue-agent-sandbox:dev}"
DIR="$ROOT/eval/tasks/validator-$ID"
J="$DIR/instance.json"; FIX="$DIR/fix.patch"; TST="$DIR/test.patch"; REPRO="$DIR/repro_test.go"
```
Set up paths. `IMG` is the pinned Go-1.24 sandbox image (overridable via the
`SANDBOX_IMAGE` env var, defaulting to `:dev`).

```bash
if [ -f "$REPRO" ]; then
  MODE=repro
  RE="^($(grep -oE 'func Test[A-Za-z0-9_]+' "$REPRO" | sed -E 's/func //' | paste -sd '|' -))\$"
else
  MODE=patch
  RE="$(python3 -c "import json;f=json.load(open('$J'))['FAIL_TO_PASS'];print('^('+'|'.join(f)+')\$')")"
fi
```
**Decide the flavor and build the `FAIL_TO_PASS` regex** `RE`. Repro flavor reads
the names directly from the `.go` file (so they can never drift from the JSON);
patch flavor reads them from `FAIL_TO_PASS`. Result is like `^(TestUrl)$`.

```bash
sandbox(){ docker run --rm -v "$REPO":/workspace -v "$GOMOD":/go/pkg/mod -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$BASE"; git -C "$REPO" reset --hard --quiet "$BASE"; git -C "$REPO" clean -fdq; }
apply(){ git -C "$REPO" apply --recount --ignore-whitespace "$1" 2>/dev/null || patch -d "$REPO" -p1 --fuzz=3 < "$1"; }
install_tests(){ if [ "$MODE" = repro ]; then cp "$REPRO" "$REPO/zz_v${ID}_repro_test.go"
  elif [ -s "$TST" ]; then apply "$TST" || fail "cannot apply test patch"; fi; }
```
Helpers: `sandbox` runs a command in the pinned container (with the persistent Go
module cache mounted, §15); `reset_base` wipes the working copy back to pristine
`BASE`; `apply` applies a patch tolerantly; `install_tests` puts the thermometer
in place.

```bash
reset_base; install_tests
echo "--- [1/3] base, no code fix: tests must FAIL ---"
if sandbox "go test -run '$RE' ./..." ; then reset_base; fail "tests PASSED at base — not capturing the bug"; fi
echo "ok: fails at base"
```
**Step 1 — must fail at base.** Run only the bug's test(s) on the broken code. If
`go test` *succeeds*, the test isn't catching the bug → reject the instance.

```bash
apply "$FIX" || { reset_base; fail "cannot apply code patch"; }
echo "--- [2/3] code fix applied: FAIL_TO_PASS must PASS ---"
sandbox "go test -run '$RE' ./..." || { reset_base; fail "tests FAILED with fix applied"; }
echo "ok: passes with fix"
```
**Step 2 — must pass with the fix.** Apply `fix.patch` on top and rerun; now the
bug test(s) must pass.

```bash
PTP="$(python3 -c "import json;print('|'.join(json.load(open('$J')).get('PASS_TO_PASS',[])))")"
if [ -n "$PTP" ]; then
  echo "--- [3/3] regression guard: PASS_TO_PASS must still PASS ---"
  sandbox "go test -run '^($PTP)\$' ./..." || { reset_base; fail "PASS_TO_PASS regressed"; }
  echo "ok: PASS_TO_PASS holds ($(python3 -c "import json;print(len(json.load(open('$J')).get('PASS_TO_PASS',[])))") tests)"
fi
reset_base
echo "PASSED: gate-gt for validator-$ID"
```
**Step 3 — the regression guard (new).** Build a giant anchored regex from the
JSON's `PASS_TO_PASS` (`^(TestA|TestB|…|Test263)$`) and run it with the fix still
applied. If any of those tests now fail, the fix regressed something → reject.
Otherwise print the count and pass.

**A known limitation of step 3, and how we close it.** Step 3 runs all the guard
tests in *one* regex. If one of the ~250 names were a typo, the regex would simply
not select it; the rest would pass; `go test` would exit 0 — and the bad name
would go unnoticed. Step 3 cannot catch a dead name. That blind spot is exactly
what `audit_gt.sh` (§13) closes by *counting* how many guard tests actually ran.

**In one sentence:** `verify_gt.sh` proves an instance is real by showing its test
*fails on the broken code*, *passes once the gold fix is applied*, and that the
fix *breaks none of its regression tests* — refusing the instance if any
expectation is violated.

---

<a name="13-audit"></a>
## 13. `audit_gt.sh` — the paranoid re-check, in depth

`verify_gt.sh` proves the *experiment* works. `audit_gt.sh` answers a different,
suspicious question: **is the answer-key data itself well-formed and fully live?**
Run it once over all instances: `bash scripts/audit_gt.sh`.

### 13.1 Why a separate audit at all

Two reasons. First, `verify_gt.sh`'s step-3 regex has the dead-name blind spot
above — it can't tell a 263-name guard from a 262-real-plus-1-typo guard. Second,
the JSON could in principle drift from the loose files, or contain a malformed
field, and we want one command that refuses to bless the answer key unless every
invariant holds. The audit is the "trust, but verify — then verify again" layer.

### 13.2 What it checks (two parts)

**Part A — static invariants (no Docker, instant).** For each instance it asserts:

| invariant | why it matters |
|---|---|
| all required fields present | a half-written instance can't be scored |
| `base_commit` is 40 hex chars | catches a truncated/garbage commit |
| `FAIL_TO_PASS` non-empty | every bug must have a thermometer |
| `PASS_TO_PASS` non-empty | the guard must actually exist |
| `PASS_TO_PASS` sorted + de-duplicated | canonical form; catches accidental dupes |
| no `FAIL_TO_PASS` name appears in `PASS_TO_PASS` | the bug tests must not leak into the guard |
| every `PASS_TO_PASS` name matches `Test[A-Za-z0-9_]+` | catches malformed names early |
| embedded `patch` == loose `fix.patch` byte-for-byte | the JSON and the file can never silently diverge |

**Part B — deep liveness (one Docker run per instance).** This is the part that
closes the blind spot. For each instance it checks out base, installs the gold
test, applies the fix, then runs the guard *verbosely* and **counts**:

```bash
go test -v -run '^(<all PASS_TO_PASS names>)$' .
# then:  NPASS = count of '^--- PASS: ' lines ; NFAIL = count of '^--- FAIL: '
# assert  NPASS == len(PASS_TO_PASS)  AND  NFAIL == 0
```

If a name were a typo, it would select no test, `NPASS` would come up short of the
list length, and the audit would flag a mismatch. So the audit proves not just
"the guard passes" but "**every single name in the guard is a real test that
actually ran and passed.**"

### 13.3 Worked example — the real output

```
===== validator-1444 =====
  [ok] has all required fields
  [ok] base_commit is 40-hex
  [ok] FAIL_TO_PASS non-empty
  [ok] PASS_TO_PASS non-empty
  [ok] PASS_TO_PASS sorted+deduped
  [ok] no FAIL_TO_PASS in PASS_TO_PASS
  [ok] all PTP names valid Test fns
  [ok] embedded patch == fix.patch file
  PASS_TO_PASS count = 263 ; FAIL_TO_PASS = ['TestUrl']
  [ok] deep liveness: 263/263 PASS_TO_PASS tests actually ran & passed (0 failed)
…
AUDIT RESULT: ALL CHECKS PASSED  ✅  (Phase A is sound)
```

The decisive line is `263/263 … 0 failed`: the count of tests that actually
emitted `--- PASS:` equals the list length, so no name is dead. Across the set the
audit confirmed `265/265`, `242/242`, `243/243`, `263/263`, `271/271`.

### 13.4 What the audit deliberately does *not* re-do

It does not re-run the fail-at-base step (that's `verify_gt.sh`'s job) — it focuses
on data integrity and guard liveness, the two things the verifier can't fully
guarantee. The two scripts are complementary: `verify_gt.sh` proves the
fail→pass→no-regress *experiment*; `audit_gt.sh` proves the *answer-key data* is
well-formed and every guard name is live.

---

<a name="14-probe"></a>
## 14. `probe_ptp.py` — proving the guard is *enforced*, in depth

There is one more question neither of the above answers: **does the thing that
will score the agent actually run `PASS_TO_PASS` and let it change the verdict?**
A perfectly derived, fully live guard is useless if the scorer silently ignores
it. `probe_ptp.py` proves it does not. Run as
`python scripts/probe_ptp.py eval/tasks/validator-1444`.

### 14.1 Why this is a distinct, necessary check

`build_ptp.sh` proves the guard is *correctly derived*. `audit_gt.sh` proves it is
*well-formed and live*. But the harness that will score candidates (Stage 2's
`run_eval.py`) is *separate code*. It could, through a bug, compute `resolved`
from `FAIL_TO_PASS` alone and never look at `PASS_TO_PASS`. Reading the source
suggests it does the right thing — but "suggests" is not "proves." This probe
provides the proof, by the only decisive method: **force a `PASS_TO_PASS` test to
fail and confirm the verdict flips to `unresolved`.**

### 14.2 How it works (the stub technique)

Running the real 263 tests in Docker just to prove the *wiring* would be slow and
would conflate "the tests pass" (already proven by the audit) with "the harness
uses them." So the probe **stubs** the Docker/git layer: it replaces the
sandbox-run function with a fake that simply *records the command it was asked to
run* and returns a controllable pass/fail. This makes the probe instant and lets
it answer precisely one question — *what does the harness do with `PASS_TO_PASS`?*
— without re-running anything.

It then evaluates the gold candidate twice:

1. **all runs succeed** → record which `go test` commands were issued, and the
   resulting `status`/`resolved`;
2. **only the `PASS_TO_PASS` run is forced to fail** → record the resulting
   `status`/`resolved`.

### 14.3 What the three results mean

| result | what it shows | why it matters |
|---|---|---|
| RESULT 1 | the harness issues a `FAIL_TO_PASS` run **and** a separate `PASS_TO_PASS` run (e.g. 263 names) | proves the guard is actually executed, not skipped |
| RESULT 2 | with everything passing → `status=resolved`, `resolved=True` | the normal good outcome |
| RESULT 3 | with the `PASS_TO_PASS` run forced to fail → `status=unresolved`, `resolved=False` | **decisive**: a bug-fixing candidate that regresses a guard test is correctly rejected |

If `PASS_TO_PASS` were ignored, RESULT 3 would still say `resolved` — the probe
would fail its own assertion and exit non-zero.

### 14.4 Worked example — the real output (on instance 1444)

```
instance      : validator-1444
FAIL_TO_PASS  : ['TestUrl']
PASS_TO_PASS  : 263 tests (probing with 'TestASCIIValidation')

RESULT 1 — what the harness ISSUES:
   issued a FAIL_TO_PASS run with 1 test name(s)
   issued a PASS_TO_PASS run with 263 test name(s)
   -> a PASS_TO_PASS test run was issued: True
RESULT 2 — all tests pass  -> status=resolved  resolved=True
RESULT 3 — PASS_TO_PASS forced to FAIL -> status=unresolved  resolved=False

VERDICT: ✅  run_eval DOES run PASS_TO_PASS and folds it into `resolved`.
```

### 14.5 How the four guard checks fit together

| script | proves | layer |
|---|---|---|
| `build_ptp.sh` | the guard is correctly *derived* (intersection method) | data construction |
| `audit_gt.sh` | the guard is well-formed and every name is *live* (count matches) | data integrity |
| `verify_gt.sh` step 3 | the gold fix does not *regress* the guard | the experiment |
| `probe_ptp.py` | the scorer actually *enforces* the guard (verdict flips when it fails) | the harness wiring |

Together they answer derived-correctly, well-formed-and-live, not-regressed, and
actually-enforced — the four independent ways the regression guard could have been
wrong, each closed.

> Note: `probe_ptp.py` imports the Stage-2 harness, so run it with the project's
> `python` (the environment that has `python-dotenv`), not a bare `python3`. It is
> documented here because it is about the `PASS_TO_PASS` data built in Stage 1,
> even though the code it exercises lives in Stage 2.

---

<a name="15-docker"></a>
## 15. The Docker sandbox command, dissected

The single most important line in the verifier is how it runs Go in isolation:

```bash
docker run --rm -v "$REPO":/workspace -v "$GOMOD":/go/pkg/mod -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; go test -run "..." ./...'
```

| piece | meaning / why |
|---|---|
| `docker run` | start a fresh container from an image |
| `--rm` | delete the container when the command finishes — no leftovers; a bad run is undone by the container vanishing |
| `-v "$REPO":/workspace` | **bind-mount** the host's validator checkout into the container at `/workspace` |
| `-v "$GOMOD":/go/pkg/mod` | **mount the persistent Go module cache** so dependencies are downloaded once per machine, not once per run (this is what makes repeated `PASS_TO_PASS` runs fast) |
| `-w /workspace` | set the working directory so `go test ./...` runs against the mounted code |
| `"$IMG"` | the pinned image (`go-issue-agent-sandbox:dev`, Go 1.24) from Stage 0 |
| `bash -c '<cmd>'` | run `<cmd>` in a **non-login** shell |
| `export PATH="/usr/local/go/bin:…"` | make sure the `go` binary is findable |

Two subtleties that caused real bugs earlier and are deliberately handled here:

- **`bash -c`, not `bash -lc`.** A *login* shell (`-lc`) re-reads profile files
  which, in this image, reset `PATH` and drop `/usr/local/go/bin` — so `go`
  becomes "command not found." A non-login shell keeps the image's environment
  intact. We also prepend the Go bin dir defensively.
- **The container is throwaway and the host is untouched in any lasting way.**
  Because of `--rm` and the bind mount, the only persistent effect is on the
  mounted checkout (which we `reset_base` afterward) and the module cache (pure
  speed). The agent's future code edits can therefore never harm the host — they
  live and die inside a container.

---

<a name="16-gate"></a>
## 16. The gate (`gate-gt`) and what each bug proved

**Definition of the gate.** For *every* instance the test must **fail** at
`base_commit` (the bug is genuinely present), **pass** after applying `fix.patch`
(the fix genuinely works), and the instance's **`PASS_TO_PASS` tests must still
pass** with the fix in (the fix breaks nothing that already worked). An instance
that cannot be made to fail at base is not capturing its bug and is dropped.

**The verified result — all five green, each "fail" arising differently, each
guard intact:**

| id | mode | how it FAILED at base | PASS_TO_PASS held | result |
|----|------|-----------------------|-------------------|--------|
| 1314 | repro | assertion: a valid postcode was rejected | 242/242 | PASSED |
| 1476 | repro | assertion: `+0123456789` was accepted | 271/271 | PASSED |
| 1444 | patch | assertion: `file://` accepted, contradicting expected `false` | 263/263 | PASSED |
| 1423 | patch | **panic**: `field.Interface()` crashed on a private field | 243/243 | PASSED |
| 1284 | patch | **compile error**: tests referenced `VarWithKey`, which didn't exist | 265/265 | PASSED |

When all five passed (verified by `verify_gt.sh`, audited by `audit_gt.sh`, and
enforcement-proven by `probe_ptp.py`), we locked the state with the git tag
**`gate-gt`**. A tag is a permanent bookmark; `git reset --hard gate-gt` returns
the repo to this exact known-good point at any time. "The dev set is grounded"
now means: five bugs, each with a thermometer proven to read sick on broken code
and healthy on fixed code, **and** a regression guard of 242–271 live tests proven
to stay green — frozen behind a tag.

---

<a name="17-env"></a>
## 17. Environment, gotchas, and reproducibility notes

**What Stage 1 depends on:**

| dependency | value | why |
|---|---|---|
| sandbox image | `go-issue-agent-sandbox:dev` | the pinned container from Stage 0 |
| Go toolchain | **1.24** | recent validator commits declare `go >= 1.24.0`; the image pins `GOTOOLCHAIN=local`. Older code still builds on 1.24 (Go is backward-compatible). |
| `gh` (GitHub CLI) | authenticated | how `build_gt.sh` fetches PR/issue facts |
| Docker | running | the sandbox boundary |
| module cache | `.cache/gomod` | mounted into the container so deps download once, not per run |
| local clone | `.cache/repos/validator` | one clone reused across all verifications |

**Real gotchas we hit and fixed (reproducibility notes):**

| symptom | root cause | fix |
|---|---|---|
| `go.mod requires go >= 1.24.0 (running 1.22.5; GOTOOLCHAIN=local)` | recent bases need Go 1.24; image had 1.22 and won't auto-upgrade | bumped the sandbox base to `golang:1.24`, retagged `:dev` |
| 1444 & 1423 produced an empty `FAIL_TO_PASS` | their PRs **modified** existing tests; the name-grep only saw *added* `func Test…` | added a fallback reading the function name from the `@@` hunk header |
| `build_gt.sh` silently aborted before writing JSON | under `set -e`, a `grep` with no match returns non-zero | appended `\|\| true` so "no match" is allowed |
| 1314 "verified" without running the test (`[no tests to run]`) | the verifier didn't load the authored `repro_test.go`, so the regex matched nothing and exited 0 | made the verifier prefer `repro_test.go` and auto-detect its name |
| 1476 "passed at base" | the PR's `TestE164` didn't exercise the `+0…` case | authored a targeted `repro_test.go` |
| **1284 `PASS_TO_PASS` would derive empty** | with the test patch installed, the base doesn't compile (`VarWithKey` undefined) → no `--- PASS:` lines | run the `build_ptp.sh` **baseline without the gold test** so the base compiles (§11.2) |
| **a `PASS_TO_PASS` typo could slip past the gate** | step-3's single regex silently skips an unmatched name | `audit_gt.sh` deep-liveness *counts* runs and asserts `count == list length` (§13) |
| `probe_ptp.py` raised `ModuleNotFoundError: dotenv` | it imports the project, but was run with a bare `python3` lacking deps | run with the project's `python` (the activated env) |

---

<a name="18-inspect"></a>
## 18. Inspect and re-run it yourself

| goal | command |
|---|---|
| pretty-print an instance | `python3 -m json.tool eval/tasks/validator-1314/instance.json` |
| see only the bug report (what the agent sees) | `python3 -c "import json;print(json.load(open('eval/tasks/validator-1314/instance.json'))['problem_statement'])"` |
| see the gold fix | `cat eval/tasks/validator-1314/fix.patch` |
| see how big a regression guard is | `python3 -c "import json;print(len(json.load(open('eval/tasks/validator-1444/instance.json'))['PASS_TO_PASS']))"` |
| derive PASS_TO_PASS for one bug | `bash scripts/build_ptp.sh 1314` |
| re-verify one bug (3-step gate) | `bash scripts/verify_gt.sh 1314` |
| re-verify all five (quiet) | `for id in 1314 1284 1476 1444 1423; do printf "%-6s " "$id:"; bash scripts/verify_gt.sh "$id" 2>&1 \| tail -1; done` |
| paranoid audit of the whole answer key | `bash scripts/audit_gt.sh` |
| prove the guard is enforced by the scorer | `python scripts/probe_ptp.py eval/tasks/validator-1444` |

---

<a name="19-unlocks"></a>
## 19. What Stage 1 unlocks, and the road ahead

- **An objective, mechanical, *two-sided* definition of "correct."** A patch is
  good only if it flips the failing test to passing **and** leaves the ~250
  regression tests green. Both halves are settled in advance and impossible to
  fudge.
- **A reusable, trustworthy instrument.** The fail→pass→no-regress mechanism in
  `verify_gt.sh` becomes, in Stage 2, the eval harness that scores *any* candidate
  patch — and the probe has already proven that harness enforces the guard.
- **A safety net with four independent proofs.** Derived (`build_ptp.sh`),
  well-formed and live (`audit_gt.sh`), not-regressed (`verify_gt.sh` step 3),
  and enforced (`probe_ptp.py`). `gate-gt` is the revertible checkpoint that
  freezes all of it.

**Next stage (only with your go-ahead): Stage 2 — the eval harness.** It wraps
this fail→pass→no-regress check in code (`run_eval.py` + `metrics.py`) so that,
given a candidate patch, it automatically applies it, runs the build/vet/fmt
gates and the `FAIL_TO_PASS`/`PASS_TO_PASS` tests, classifies the result with a
`status` and tri-state gates, and prints a metrics table. Its own gate is a
self-test: feed it the gold patches and all must score `resolved`; feed it empty
patches and none may — proving the ruler itself is trustworthy before we ever
point it at the agent. See `docs/stage-2.md`.

---

*This document is meant to be exhaustive. If any single paragraph, table row,
diff, or line of script is still unclear, name it and I will expand that one spot
further. We remain on Stage 1/2 documentation until you are fully satisfied — no
advancing to Stage 3 without your say-so.*
