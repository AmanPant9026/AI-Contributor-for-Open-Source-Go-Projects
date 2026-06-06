# Stage 1 — Ground Truth: The Complete, Detailed Reference

> **Status:** complete and locked at git tag `gate-gt`.
> **One-line summary:** before writing any agent, we built and *proved correct* an
> answer key of five real, already-fixed bugs from `go-playground/validator`, so
> that every future claim about the agent can be measured objectively.

This is the long-form reference. It explains every concept from first
principles, dissects the real files and scripts on your disk line by line, and
works through concrete micro-examples (a diff character by character, a regex
symbol by symbol, the commit graph drawn out). If you read it top to bottom you
should understand not just *what* each file is but *why it has to exist* and
*how the machinery actually runs*.

---

## Table of contents

1. [The problem Stage 1 solves, from scratch](#1-the-problem)
2. [Core vocabulary, explained properly](#2-vocabulary)
3. [How Go testing actually works](#3-go-testing)
4. [How git history works, and why `^1` is the broken version](#4-git-history)
5. [How to read a unified diff (worked character by character)](#5-diffs)
6. [The five bugs — real root-cause walkthroughs](#6-the-five-bugs)
7. [The instance schema (every JSON field, in depth)](#7-schema)
8. [The two flavors, and the `repro_test.go` question answered fully](#8-flavors)
9. [The folder layout and every file](#9-files)
10. [`build_gt.sh` — the fetcher, line by line](#10-build_gt)
11. [`verify_gt.sh` — the prover, line by line](#11-verify_gt)
12. [The Docker sandbox command, dissected](#12-docker)
13. [The gate (`gate-gt`) and what each bug proved](#13-gate)
14. [Environment, gotchas, and reproducibility notes](#14-env)
15. [Inspect and re-run it yourself](#15-inspect)
16. [What Stage 1 unlocks, and the road ahead](#16-unlocks)

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
   the maintainers merged that fix), and
3. a concrete test that *fails* while the bug is present and *passes* once it is
   fixed — a thermometer that objectively reads "sick" or "healthy."

Once we possess that, grading the agent becomes mechanical and honest: run the
agent on the *same* bug, take whatever code change it produces, and ask the
thermometer — does the failing test now pass? Did the agent change the same files
the human did? This is exactly, deliberately, how the assignment says *we* will
be graded (compare the agent's output to accepted PRs on a set of axes), so our
ruler mirrors the grader's ruler.

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
(`instance_id`, `base_commit`, `FAIL_TO_PASS`, a `tasks/` folder) come from
**SWE-bench**, the standard academic benchmark for exactly this task —
"can a system resolve a real GitHub issue?" Reusing its shape means our numbers
are comparable to published work and recognizable to a reviewer.

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
| Sandbox | the pinned Docker container where Go is compiled and tested in isolation |
| Test | a function the Go toolchain runs to assert the code behaves correctly |
| FAIL_TO_PASS | the test(s) that must change from failing → passing |
| PASS_TO_PASS | tests that were passing and must keep passing |
| Instance | one bug packaged as data (report + fix + test + metadata) |

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

**Issue vs Pull Request.** An *issue* is a complaint: "feature X is broken, here's
how to reproduce it." A *pull request* is a proposed code change, often the one
that fixes an issue. The maintainers review PRs; when they accept one they
**merge** it, meaning its changes become part of the project's history. A
*merged* PR is gold for us because its changes were reviewed and accepted —
they are a known-correct fix, not a random guess.

**Sandbox.** Built in Stage 0. It is a Docker container with a fixed Go
toolchain. We compile and run the target project *inside* it so that (a) results
are reproducible regardless of what's installed on the host Mac, and (b)
untrusted or experimental code can never touch the host. Think of it as a clean,
disposable lab bench.

---

<a name="3-go-testing"></a>
## 3. How Go testing actually works

Everything in Stage 1 ultimately bottoms out in running Go tests, so it's worth
understanding the mechanics precisely.

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
| `go test ./...` | build and run **every** test in **every** package under the current directory (`./...` = "this module, recursively") |
| `go test -run '<regex>' ./...` | only run test functions whose **name matches** the regular expression |
| `go build ./...` | compile every package, but do **not** run tests |

**The `-run` regex.** `-run` takes a regular expression and runs only the tests
whose names match it. We always build an *anchored* pattern like
`^(TestUrl)$` — `^` means "start of name," `$` means "end," and `(A|B)` means "A
or B." So `^(TestValidate_VarWithKey|TestValidate_VarWithKeyCtx)$` runs exactly
those two functions and nothing else. We do this so the verifier checks *only*
the bug's own test, not the project's thousands of unrelated tests.

**Exit codes — the actual pass/fail signal.** Every command-line program returns
an integer "exit code" when it finishes: `0` conventionally means success,
non-zero means failure. `go test` exits **0** if all selected tests pass, and
**non-zero** if *any* selected test fails. Our scripts read that exit code; they
do not parse the human-readable output. This is robust: we don't have to
understand Go's printout, just whether the number was zero.

**A subtle trap — "[no tests to run]".** If the `-run` regex matches *no* test
(for example because the test file wasn't actually loaded), `go test` prints
`[no tests to run]` and exits **0** — success! This bit us once: an early version
of the verifier for bug #1314 didn't load our authored test, so the regex matched
nothing, `go test` exited 0, and the script wrongly concluded "the test passed at
base." We fixed it by making the verifier explicitly install the test first. The
lesson baked into the current script: a vacuous pass is still a pass to `go
test`, so you must ensure the test is genuinely present.

**Three ways a test can "fail at base."** When we run the bug's test on the
*broken* code, "fail" can manifest three different ways, and all three are valid:

| failure mode | what happens | which bug shows this |
|---|---|---|
| assertion failure | the test runs, an `if` check trips, `t.Fatalf` fires → non-zero exit | 1314, 1476, 1444 |
| panic (crash) | the code under test crashes; Go marks the test failed → non-zero exit | 1423 |
| compile error | the test references a symbol that doesn't exist yet → the package won't build → non-zero exit | 1284 |

In every case `go test` exits non-zero, which is all "fail at base" requires.

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

---

<a name="5-diffs"></a>
## 5. How to read a unified diff (worked character by character)

A `.patch` file is a **unified diff** — a compact recipe of edits. Understanding
it is essential because `fix.patch` and `test.patch` are both diffs.

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
| `+++ b/greeter.go` | header | the "after" file |
| `@@ -3,5 +3,6 @@ func Greet…` | hunk header | this *hunk* (block of changes) covers 5 lines starting at line 3 in the old file, 6 lines starting at line 3 in the new file; the trailing text names the enclosing function |
| ` 	if name == "" {` | space prefix | **context** — unchanged, shown so the tool can locate the edit precisely |
| `-	return "Hi " + name` | `-` prefix | this line is **removed** |
| `+	greeting := "Hello, "` | `+` prefix | this line is **added** |
| `+	return greeting + name` | `+` prefix | this line is **added** |

So the recipe reads: "in `greeter.go`, around line 3, delete `return "Hi " +
name` and insert the two new lines, keeping the surrounding `if` block as it is."
A line with neither `+` nor `-` is just there for *positioning* — it isn't
changed.

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
the root cause, the fix, and how the test catches it.

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
  `TestE164`, but its cases (`"+12025550123"`, `"0123456789"`, `"++…"`,
  `"+1 202-555-0123"`) all happen to give the *same* verdict under both the old
  and new regex — so `TestE164` passes even on the broken code and can't detect
  the bug. Our `repro_test.go` targets the one input that distinguishes them,
  `"+0123456789"`, and asserts it's rejected.

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

### 6.4 Bug 1423 — crash when validating a private struct field

- **Symptom.** With private-field validation enabled, validating a struct that
  has an unexported (lowercase) field *crashes the program* instead of returning
  a normal validation error.
- **Root cause.** The engine read field values with `field.Interface()`. Go's
  reflection refuses to hand out the value of an *unexported* field via
  `.Interface()` — it **panics** with "reflect: reflect.Value.Interface: cannot
  return value obtained from unexported field or method." So any unexported field
  reached this line and crashed.
- **Fix.** A larger refactor (~538 lines across ~5 files including `validator.go`)
  replacing `field.Interface()` with a new helper `getValue()` that reads
  unexported fields safely. This is our deliberately *big, multi-file* instance,
  proving the agent (later) and the harness handle more than one-liners.
- **How the test catches it (reused `test.patch`).** The PR added cases to
  `TestPrivateFieldsStruct` using private map/pointer fields. On broken code the
  first such case hits `field.Interface()` and **panics** → the test fails by
  crash. With the fix, `getValue()` reads them safely → the test returns the
  expected errors → passes. (`TestImageValidation` is also in the patch but
  passes both ways; it's harmless to include.)

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

---

<a name="7-schema"></a>
## 7. The instance schema (every JSON field, in depth)

Each bug is one `instance.json`. Below is every field with the real #1314 value
and a full explanation of its role.

| field | example (#1314) | role — in depth |
|---|---|---|
| `instance_id` | `go-playground__validator-1314` | a globally-unique label, in SWE-bench's `owner__repo-number` style. Used to name results and group artifacts. Purely an identifier. |
| `repo` | `go-playground/validator` | which project this bug is from. Lets the harness know what to clone. |
| `base_commit` | `2cce309b681d…` | the snapshot **where the bug still exists** (§4). The verifier and (later) the agent both start here. This is the most operationally important field. |
| `problem_statement` | "Bug: postcode… broken in v10.21.0 …" | the bug report. **The one and only field the agent is allowed to read.** It must describe the *symptom* like a user would, never the fix. |
| `patch` | the one-line `baked_in.go` diff | the gold **code fix** (no test files). The thing the agent's fix is compared against. Hidden from the agent. |
| `test_patch` | a new-file diff adding the repro | the gold **test**, stored as a diff so it can be applied. Hidden. |
| `FAIL_TO_PASS` | `["TestIssue1314PostcodeIso3166Alpha2Field"]` | the **headline grade**: the test name(s) that must go fail→pass. Hidden. |
| `PASS_TO_PASS` | list of test names | tests that were already green and must **stay** green (regression guard). Derived by `scripts/build_ptp.sh` (see below) — the tests in the fix's package(s) that pass both at base and at base+fix, minus `FAIL_TO_PASS`. Hidden. |
| `go_version` | `"1.24"` | which Go toolchain to use (recent validator needs ≥1.24, §14). |
| `issue` | `1314` | the GitHub issue number (provenance). |
| `fix_pr` | `1359` | the PR that fixed it (provenance). |
| `merge_commit` | `b111154…` | the commit that merged the fix (provenance). |

> **Restate the golden rule, because it's the soul of the design:** the agent
> sees **only** `problem_statement` + the code at `base_commit`. The `patch`,
> `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS` are the hidden answer key.
> Showing any of them to the agent would be cheating — like grading a student
> who was handed the answer sheet.

**Self-containment.** `instance.json` embeds full copies of the patch and test
text inside it (as the `patch` and `test_patch` strings). So the JSON *alone* is
a complete instance. The loose `fix.patch` / `test.patch` / `repro_test.go` files
next to it hold the same content as standalone files, purely because the shell
scripts find it convenient to `git apply` a file rather than a JSON string.

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
code and passes on fixed code (§13 shows the proof). And in both, the *fix*
(`fix.patch`) is always the **real human fix**; only the *test* is sometimes
ours. Bug 1476 happens to carry **both** a `test.patch` (the weak reused one,
kept for reference) and a `repro_test.go` (the one we actually use). The verifier
always prefers `repro_test.go` when present.

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
| `instance.json` | JSON | **the instance** — the self-contained answer key (§7) | `build_gt.sh` (or by hand for 1314) | yes |
| `fix.patch` | unified diff | **the cure** — the real code fix (no tests) | `build_gt.sh` splits it from the PR | yes |
| `test.patch` | unified diff | **the thermometer, Flavor A** — the PR's own test | `build_gt.sh` splits it from the PR | yes |
| `repro_test.go` | full Go file | **the thermometer, Flavor B** — the test we wrote | written by hand | yes |
| `tests.txt` | plain text | scratch: detected test-function names | `build_gt.sh` | no (git-ignored) |
| `src.json` | JSON | scratch: raw issue/PR text from `gh` before cleanup | `build_gt.sh` | no (git-ignored) |

**The two scratch files in detail:**

- `tests.txt` — when `build_gt.sh` reads a `test.patch`, it greps out the test
  function names and writes them here as an intermediate step, then copies them
  into the JSON's `FAIL_TO_PASS`. After that it has no further use. It's
  git-ignored so it never clutters a commit. Think of it as the script's sticky
  note to itself.
- `src.json` — the raw `{"title": …, "body": …}` JSON that `gh` returned for the
  issue or PR, before we trimmed/cleaned it into the `problem_statement` string.
  Kept only so we can re-check the original wording if we ever suspect the
  cleanup lost something. Also git-ignored.

So when the folder looks busy, remember: **one `instance.json` is the real
artifact; `fix.patch`/`test.patch`/`repro_test.go` are convenience copies of
content already inside the JSON; `tests.txt`/`src.json` are disposable notes.**

(The §7.x deep code walkthroughs of `fix.patch`, `test.patch`, and
`repro_test.go` are folded into §5 and §6 above, where each is shown against its
real bug.)

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
`ROOT` resolves the repo root relative to the script's own location (so it works
from any directory). `REPO` is the local clone of validator. `DIR` is this bug's
output folder, created with `mkdir -p`.

```bash
command -v gh >/dev/null || { echo "need gh"; exit 1; }
[ -d "$REPO/.git" ] || { echo "validator clone missing; run 'make check-env' once"; exit 1; }
```
Preconditions: the `gh` CLI must be installed, and the local validator clone must
exist (Stage 0's env check creates it).

```bash
MC="$(gh pr view "$PR" --repo "$SLUG" --json mergeCommit --jq '.mergeCommit.oid')"
[ -n "$MC" ] || { echo "no merge commit (is #$PR MERGED?)"; exit 1; }
```
Ask GitHub (via `gh`) for the PR's **merge commit** hash. `--json mergeCommit`
requests that field; `--jq '.mergeCommit.oid'` extracts the commit id. If empty,
the PR was never merged → stop (we only build from merged PRs).

```bash
git -C "$REPO" fetch --all --tags --quiet
git -C "$REPO" cat-file -e "$MC" 2>/dev/null || git -C "$REPO" fetch --quiet origin "$MC" 2>/dev/null || true
BASE="$(git -C "$REPO" rev-parse "${MC}^1")"
```
Make sure our local clone has the relevant commits (`fetch`), then compute
`BASE = MC^1` — the merge commit's first parent, i.e. the broken pre-fix snapshot
(§4). `git -C "$REPO"` runs git inside the clone.

```bash
git -C "$REPO" diff "${MC}^1" "$MC" -- ':(exclude)*_test.go' > "$DIR/fix.patch"
git -C "$REPO" diff "${MC}^1" "$MC" -- '*_test.go'           > "$DIR/test.patch"
```
The heart of the split. `git diff BASE MC` is the PR's entire change. The
`-- <pathspec>` part filters which files:
- `':(exclude)*_test.go'` → everything **except** test files → the **code fix** →
  `fix.patch`.
- `'*_test.go'` → **only** test files → the **gold test** → `test.patch`.

This cleanly separates "the cure" from "the thermometer."

```bash
ADDED="$(grep -E '^\+func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/^\+func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true)"
if [ -n "$ADDED" ]; then
  printf '%s\n' "$ADDED" > "$DIR/tests.txt"
else
  { grep -E '^@@.*func Test' "$DIR/test.patch" 2>/dev/null | sed -E 's/.*func (Test[A-Za-z0-9_]+).*/\1/' | sort -u || true; } > "$DIR/tests.txt"
fi
```
Work out the `FAIL_TO_PASS` test names from the test patch:
- First try lines like `+func TestFoo` — tests the PR **added** (the `grep` finds
  them, the `sed` extracts the name). This handles 1284 (new tests).
- If there are none (the PR only *modified* existing tests, like 1444/1423), fall
  back to reading the function name from the `@@ … func TestBar …` hunk header.
- The trailing `|| true` is essential: under `set -e`, a `grep` that finds nothing
  returns non-zero and would kill the whole script. `|| true` makes "no match" an
  acceptable outcome instead of a fatal error. (This exact bug — the script
  aborting silently before writing the JSON — is why 1444/1423 first came out
  empty; see §14.)

```bash
ISSUE="$(gh pr view "$PR" --repo "$SLUG" --json closingIssuesReferences --jq '.closingIssuesReferences[0].number // empty' 2>/dev/null || true)"
if [ -n "${ISSUE:-}" ]; then
  gh issue view "$ISSUE" --repo "$SLUG" --json title,body > "$DIR/src.json"; echo "problem_statement <- issue #$ISSUE"
else
  gh pr view "$PR" --repo "$SLUG" --json title,body > "$DIR/src.json"; echo "problem_statement <- PR #$PR (REVIEW for fix leakage)"
fi
```
Get the text for `problem_statement`. We **prefer the linked issue** (the user's
own words describing the symptom). If the PR closes an issue, fetch that issue's
title+body. Otherwise we fall back to the PR body and print a `REVIEW for fix
leakage` warning — because a PR description sometimes explains the *solution*,
which we must not feed the agent. (Trimming any such leakage is a later polish
step; it doesn't affect the gate.) The raw text lands in `src.json`.

```bash
python3 - "$ID" "$PR" "$BASE" "$MC" "$DIR" "${ISSUE:-}" <<'PY'
import json,sys
ID,PR,BASE,MC,DIR,ISSUE=sys.argv[1:7]
src=json.load(open(f"{DIR}/src.json"))
ps=((src.get("title") or "")+"\n\n"+(src.get("body") or "")).strip()
ftp=[l.strip() for l in open(f"{DIR}/tests.txt") if l.strip()]
inst={"instance_id":f"go-playground__validator-{ID}","repo":"go-playground/validator","base_commit":BASE,
 "problem_statement":ps,"patch":open(f"{DIR}/fix.patch").read(),
 "test_patch":open(f"{DIR}/test.patch").read(),"FAIL_TO_PASS":ftp,"PASS_TO_PASS":[],
 "go_version":"1.24","fix_pr":int(PR),"issue":(int(ISSUE) if ISSUE else None),"merge_commit":MC}
json.dump(inst,open(f"{DIR}/instance.json","w"),indent=2,ensure_ascii=False)
print("wrote validator-%s/instance.json | FAIL_TO_PASS=%s"%(ID,ftp))
PY
```
A small embedded Python program assembles the final `instance.json`. It builds
the `problem_statement` from the fetched title+body, reads the test names out of
`tests.txt`, reads the two patch files, and writes everything into the JSON with
the schema from §7 (`go_version` hard-set to `"1.24"`). Python is used here
because it does the JSON escaping correctly (newlines, quotes, tabs inside the
patch strings) — something that's painful to get right in pure shell.

**In one sentence:** `build_gt.sh` asks GitHub for a merged PR, splits its diff
into a code fix and a test, figures out the test names, grabs the bug report, and
writes a complete `instance.json` — fully automated, no manual diff copying.

---

<a name="11-verify_gt"></a>
## 11. `verify_gt.sh` — the prover, line by line

### 11.0 Deriving `PASS_TO_PASS` first (`scripts/build_ptp.sh`)

Before an instance can be fully verified, its regression-guard list is derived
once by `scripts/build_ptp.sh <id>`. The SWE-bench-standard method, scoped to the
package(s) the fix touches (for validator, the root package):

| step | action | why |
|---|---|---|
| 1 | find the touched packages from `fix.patch` | scope the guard to where regressions would actually appear; keeps it fast |
| 2 | at **base**, no change, run `go test -v <pkgs>` → record passing tests (`before`) | the baseline of already-stable tests. Run *without* the gold test so a `test_patch` that references not-yet-existing symbols (e.g. 1284's `VarWithKey`) can't break this compile |
| 3 | at **base + gold test + fix**, run `go test -v <pkgs>` → record passing tests (`after`) | the tests that pass once the fix is in |
| 4 | `PASS_TO_PASS = (before ∩ after) − FAIL_TO_PASS` | only tests that were stable *and* stay stable, excluding the bug tests themselves |
| 5 | write the list into `instance.json` | it becomes part of the hidden answer key |

The intersection is the key: a test only qualifies if it passed *before* and
*after*, so we never record something that was already broken or flaky.

### 11.1 The verification experiment

This is the *proof* that an instance is trustworthy. Run as
`bash scripts/verify_gt.sh <id>`. The experiment it performs:

```
reset to base  →  add the test  →  run it (must FAIL)  →  apply the fix  →  run it (must PASS)  →  clean up
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
`SANDBOX_IMAGE` env var, defaulting to `:dev`). `J/FIX/TST/REPRO` point at this
bug's files.

```bash
fail(){ echo "FAIL: $1"; exit 1; }
[ -f "$J" ] || fail "$J not found …"
BASE="$(python3 -c "import json;print(json.load(open('$J'))['base_commit'])")"
```
A helper to print a failure and stop. Read `base_commit` out of the JSON with a
one-line Python call.

```bash
if [ -f "$REPRO" ]; then
  MODE=repro
  RE="^($(grep -oE 'func Test[A-Za-z0-9_]+' "$REPRO" | sed -E 's/func //' | paste -sd '|' -))\$"
else
  MODE=patch
  RE="$(python3 -c "import json;f=json.load(open('$J'))['FAIL_TO_PASS'];print('^('+'|'.join(f)+')\$')")"
fi
[ "$RE" != '^()$' ] || fail "no test names for $ID"
```
**Decide the flavor and build the test-name regex** `RE`:
- If a `repro_test.go` exists → `MODE=repro`; derive the test names *directly from
  that file* by grepping its `func Test…` lines (`paste -sd '|'` joins multiple
  names with `|`). Reading the name from the file means it can never drift out of
  sync with the JSON.
- Otherwise → `MODE=patch`; take the names from the JSON's `FAIL_TO_PASS`.
- Either way `RE` ends up like `^(TestUrl)$`. If it's empty, bail.

```bash
sandbox(){ docker run --rm -v "$REPO":/workspace -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; '"$1"; }
reset_base(){ git -C "$REPO" checkout --force --quiet "$BASE"; git -C "$REPO" reset --hard --quiet "$BASE"; git -C "$REPO" clean -fdq; }
apply(){ git -C "$REPO" apply --recount --ignore-whitespace "$1" 2>/dev/null || patch -d "$REPO" -p1 --fuzz=3 < "$1"; }
install_tests(){
  if [ "$MODE" = repro ]; then cp "$REPRO" "$REPO/zz_v${ID}_repro_test.go"
  elif [ -s "$TST" ]; then apply "$TST" || fail "cannot apply test patch"
  else fail "no test patch / repro for $ID"; fi
}
```
Four helper functions:
- `sandbox` — run a command inside the Docker sandbox against the mounted repo
  (fully dissected in §12).
- `reset_base` — wipe the working copy back to the pristine broken state:
  `checkout --force` switches to `BASE`, `reset --hard` discards tracked changes,
  `clean -fdq` deletes untracked files (like a leftover test we copied in).
- `apply` — apply a patch tolerantly (git apply, falling back to `patch --fuzz`).
- `install_tests` — put the thermometer in place: copy `repro_test.go` into the
  checkout (Flavor B), or apply `test.patch` (Flavor A).

```bash
git -C "$REPO" cat-file -e "$BASE" 2>/dev/null || git -C "$REPO" fetch --all --tags --quiet
reset_base || fail "cannot checkout base $BASE"
install_tests
```
Make sure the base commit is available locally, reset to it, and install the
test.

```bash
echo "--- [1/2] base, no code fix: tests must FAIL ---"
if sandbox "go test -run '$RE' ./..." ; then reset_base; fail "tests PASSED at base — not capturing the bug"; fi
echo "ok: fails at base"
```
**Phase 1 — must fail at base.** Run only this bug's test(s) on the broken code.
If `go test` *succeeds* (exit 0), that's a problem: the test isn't catching the
bug → we reject the instance. (This is precisely the check that flagged 1476's
weak test.) We *want* it to fail here.

```bash
apply "$FIX" || { reset_base; fail "cannot apply code patch"; }
echo "--- [2/2] code fix applied: tests must PASS ---"
sandbox "go test -run '$RE' ./..." || { reset_base; fail "tests FAILED with fix applied"; }
echo "ok: passes with fix"
```
**Phase 2 — must pass with the fix.** Apply `fix.patch` (the gold cure) on top,
then run the same test(s) again. Now they must pass; if not, something's wrong
with the instance.

```bash
reset_base
echo ""
echo "PASSED: gate-gt for validator-$ID"
```
Clean up (restore the pristine base) and report success.

**In one sentence:** `verify_gt.sh` proves an instance is real by showing its test
*fails on the broken code* and *passes once the gold fix is applied* — and if
either expectation is violated, it refuses the instance.

---

<a name="12-docker"></a>
## 12. The Docker sandbox command, dissected

The single most important line in the verifier is how it runs Go in isolation:

```bash
docker run --rm -v "$REPO":/workspace -w /workspace "$IMG" \
  bash -c 'export PATH="/usr/local/go/bin:/go/bin:$PATH"; go test -run "..." ./...'
```

| piece | meaning / why |
|---|---|
| `docker run` | start a fresh container from an image |
| `--rm` | automatically delete the container when the command finishes — no leftovers accumulate, and a bad run is undone by the container simply vanishing |
| `-v "$REPO":/workspace` | **bind-mount** the host's validator checkout into the container at `/workspace`, so the container operates on our real files; the container itself is disposable |
| `-w /workspace` | set the working directory to `/workspace` so `go test ./...` runs against the mounted code |
| `"$IMG"` | the pinned image (`go-issue-agent-sandbox:dev`, Go 1.24) from Stage 0 |
| `bash -c '<cmd>'` | run `<cmd>` in a **non-login** shell |
| `export PATH="/usr/local/go/bin:…"` | make sure the `go` binary is findable |

Two subtleties that caused real bugs earlier and are deliberately handled here:

- **`bash -c`, not `bash -lc`.** A *login* shell (`-lc`) re-reads profile files
  which, in this image, reset `PATH` and drop `/usr/local/go/bin` — so `go`
  becomes "command not found." A non-login shell keeps the image's environment
  intact. We also prepend the Go bin dir defensively with the `export PATH` line.
- **The container is throwaway and the host is untouched in any lasting way.**
  Because of `--rm` and the bind mount, the only persistent effect is on the
  mounted checkout (which we `reset_base` afterward anyway). The agent's
  future code edits can therefore never harm the host machine — they live and die
  inside a container.

---

<a name="13-gate"></a>
## 13. The gate (`gate-gt`) and what each bug proved

**Definition of the gate.** For *every* instance: the test must **fail** at
`base_commit` (the bug is genuinely present), **pass** after applying `fix.patch`
(the fix genuinely works), and the instance's **`PASS_TO_PASS` tests must still
pass** with the fix in (the fix breaks nothing that already worked). An instance
that cannot be made to fail at base is not capturing its bug and is dropped.

**The verified result — all five green, each "fail" arising differently:**

| id | mode | how it FAILED at base | result |
|----|------|-----------------------|--------|
| 1314 | repro | assertion: a valid postcode was rejected (`t.Fatalf`) | PASSED |
| 1476 | repro | assertion: `+0123456789` was accepted (`t.Fatalf`) | PASSED |
| 1444 | patch | assertion: `file://` was accepted, contradicting the new expected `false` | PASSED |
| 1423 | patch | **panic**: `field.Interface()` crashed on a private field | PASSED |
| 1284 | patch | **compile error**: the tests referenced `VarWithKey`, which didn't exist yet | PASSED |

When all five passed, we locked the state with the git tag **`gate-gt`**. A tag is
a permanent bookmark on the project's history; `git reset --hard gate-gt` returns
the repo to this exact known-good point at any time. "The dev set is grounded"
means precisely this: five bugs, each with a thermometer proven to read sick on
broken code and healthy on fixed code, frozen behind a tag.

---

<a name="14-env"></a>
## 14. Environment, gotchas, and reproducibility notes

**What Stage 1 depends on:**

| dependency | value | why |
|---|---|---|
| sandbox image | `go-issue-agent-sandbox:dev` | the pinned container from Stage 0 |
| Go toolchain | **1.24** | recent validator commits declare `go >= 1.24.0` in `go.mod`; the official image refuses to build with an older toolchain. Older code (e.g. #1314 at v10.24.0) still builds fine on 1.24 because Go is backward-compatible. |
| `gh` (GitHub CLI) | authenticated | how `build_gt.sh` fetches PR/issue facts |
| Docker | running | the sandbox boundary |
| local clone | `.cache/repos/validator` | one clone reused across all verifications |

**Real gotchas we hit and fixed (reproducibility notes for the README):**

| symptom | root cause | fix |
|---|---|---|
| `go.mod requires go >= 1.24.0 (running 1.22.5; GOTOOLCHAIN=local)` | the recent bases need Go 1.24; the image had 1.22, and the image pins `GOTOOLCHAIN=local` so it won't auto-download a newer Go | bumped the sandbox base to `golang:1.24`, retagged the image `:dev` |
| 1444 & 1423 produced an empty `FAIL_TO_PASS` | their PRs **modified** existing tests rather than adding new `func Test…`; the name-grep only saw added functions | added a fallback that reads the enclosing function name from the `@@` hunk header |
| `build_gt.sh` silently aborted before writing the JSON | under `set -e`, a `grep` with no match returns non-zero and killed the script | appended `\|\| true` to the greps so "no match" is allowed |
| 1314 "verified" without actually running the test (`[no tests to run]`) | the generalized verifier didn't load the authored `repro_test.go`, so the regex matched nothing and `go test` exited 0 | made the verifier prefer a `repro_test.go` and auto-detect its test name |
| 1476 "passed at base" | the PR's `TestE164` didn't exercise the `+0…` case, so it couldn't catch the bug | authored a targeted `repro_test.go` for `+0123456789` |

---

<a name="15-inspect"></a>
## 15. Inspect and re-run it yourself

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

<a name="16-unlocks"></a>
## 16. What Stage 1 unlocks, and the road ahead

- **An objective, mechanical definition of "correct."** We can now score *any*
  patch — crucially, the agent's, once it exists — by whether it flips the failing
  test to passing on the same base commit. That's the headline grading axis,
  settled in advance and impossible to fudge.
- **A reusable instrument.** The exact fail→pass mechanism in `verify_gt.sh`
  becomes, in Stage 2, the eval harness that scores *any* candidate patch (not
  just the gold one) and reports the full metrics table.
- **A safety net.** `gate-gt` is a known-good, revertible checkpoint; the scratch
  files are git-ignored so the answer key stays clean and reviewable.

**Next stage (only with your go-ahead): Stage 2 — the eval harness.** It wraps
this fail→pass check in code (`run_eval.py` + `metrics.py`) so that, given a
candidate patch, it automatically applies it, runs the tests and the
build/vet/fmt gates, and prints a metrics table (resolution rate, localization
recall/precision, …). Its own gate is a self-test: feed it the gold patches and
all must score resolved; feed it empty patches and none may — proving the ruler
itself is trustworthy before we ever point it at the agent.

---

*This document is meant to be exhaustive. If any single paragraph, table row,
diff, or line of script is still unclear, name it and I will expand that one spot
further. We remain on Stage 1 until you are fully satisfied — no advancing
without your say-so.*
