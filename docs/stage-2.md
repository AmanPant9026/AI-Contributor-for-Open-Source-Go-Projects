# Stage 2 — The Ruler (Eval Harness): The Complete, Detailed Reference

> **Status:** complete; locked at git tag `gate-2` (self-check green).
> **One-line summary:** a single command that takes *any* code patch and scores
> it against the frozen Stage-1 answer key on the same axes the assignment grades
> on — classifying each result with a four-way **status** and tri-state gates so a
> miss is never ambiguous — and we prove the ruler is honest before ever pointing
> it at the agent.

This is the long-form reference, written to the same what/why/how depth as
`stage-1.md`: every concept from first principles, the real functions dissected,
worked example rows, the full story of *why* the status/tri-state model exists
(it went through several user-driven redesigns), and the gotchas we hit.

It supersedes the earlier version of this document: it adds the populated
`PASS_TO_PASS` enforcement, the `status` + tri-state `n/a` model and the design
evolution that produced it, the gate-reordering rationale, the three branches of
`evaluate`, and worked failure-mode examples.

---

## Table of contents

1. [Why this stage exists](#1-why)
2. [What it does, step by step (the generalized fail→pass dance)](#2-what)
3. [Why the gates run *before* the gold test (the reordering)](#3-reorder)
4. [The `status` + tri-state model, and the story of why it exists](#4-status)
5. [Why `resolved` is tests-only (build dropped from the verdict)](#5-resolved)
6. [The metrics, mapped to the grader's axes](#6-metrics)
7. [Calibration — why we score the gold and empty patches](#7-calibration)
8. [The files and how they fit together](#8-files)
9. [`metrics.py` — the pure scoring math, in depth](#9-metrics-py)
10. [`run_eval.py` — the orchestrator, in depth (three branches)](#10-run-eval)
11. [How `PASS_TO_PASS` is enforced (and proven enforced)](#11-ptp)
12. [The persistent module cache (why runs get fast)](#12-cache)
13. [The gate (`gate-2`) — the ruler grading itself](#13-gate)
14. [How to run it, and how to read the table](#14-run)
15. [Worked examples: reading unresolved / apply_failed rows](#15-worked)
16. [Gotchas we hit and fixed](#16-gotchas)
17. [What this is NOT (scope boundaries)](#17-scope)
18. [What Stage 2 unlocks](#18-unlocks)

---

<a name="1-why"></a>
## 1. Why this stage exists

Stage 1 produced an **answer key**: five bugs, each with a base commit, a gold
fix, a test proven to fail-when-broken / pass-when-fixed, and a regression guard
(`PASS_TO_PASS`) of ~250 tests proven live. But the only thing that knows how to
*use* that key so far is `verify_gt.sh`, and it can only check **one** patch — the
gold one. It answers "is this instance valid?", not "how good is an *arbitrary*
fix?".

When the agent starts producing fixes (Stage 4), "is its fix good?" must become a
**mechanical number**, not a judgment call. Stage 2 builds that measuring
instrument: a scorer that accepts *any* patch (gold, empty, or — later — the
agent's) and reports a row of numbers. We build it now, while the only patches in
existence are gold and empty, precisely so we can **prove the instrument is
honest** before there's an agent whose output it will judge.

> Ruler before the thing it measures. The eval harness is the agent's
> *examiner*; it is intentionally built before the agent's *eyes and hands* (the
> tree-sitter indexing stack, which is Stage 3). You calibrate the thermometer
> before you take the patient's temperature.

---

<a name="2-what"></a>
## 2. What it does, step by step (the generalized fail→pass dance)

For each instance, and for a given **candidate** patch, the harness performs the
Stage-1 fail→pass dance, generalized to score *any* patch:

| step | action | why |
|---|---|---|
| 1 | check out the repo at `base_commit` (a throwaway checkout under `.cache/`) | start from the broken version |
| 2 | apply the **candidate** code patch (gold / empty / later the agent's) | the thing being scored |
| 3 | run the code-quality gates `go build` / `go vet` / `gofmt` — **before** the gold test is installed | judge the candidate's *own* code, against the project's real tests (see §3) |
| 4 | install the bug's **gold test** — `repro_test.go` if present, else `test.patch` | the measuring instrument (same repro-preference rule as `verify_gt.sh`) |
| 5 | run the `FAIL_TO_PASS` tests **and** the `PASS_TO_PASS` tests → decide `resolved` | judge the outcome: did it fix the bug *and* break nothing? |
| 6 | turn the results into a score row, classify the `status`, then reset the checkout | the measurement + clean slate |

The only difference between candidates is **step 2**: the gold candidate applies
the real fix, the empty candidate applies nothing, the agent candidate (later)
applies whatever the agent produced. Everything else is identical — which is what
makes the comparison fair.

This is the same experiment Stage 1 proved correct, with two generalizations: the
patch under test is now *arbitrary*, and the result is *classified* (status +
tri-state gates) rather than a bare pass/fail.

---

<a name="3-reorder"></a>
## 3. Why the gates run *before* the gold test (the reordering)

This ordering — gates in step 3, gold test in step 4 — was a deliberate fix, and
it matters enough to explain on its own.

**What the gates are for.** `go build`, `go vet`, and `gofmt` are meant to judge
the *candidate's code quality* — "would this change pass the project's CI?" They
answer "does it compile / is it suspicious / is it formatted," about the code the
candidate wrote.

**What goes wrong if you install the gold test first.** Our hidden gold test
sometimes adds a *new* test that exercises a new API (e.g. 1284's `VarWithKey`).
If we installed that test and *then* ran `go vet ./...`, a perfectly clean
candidate fix that happened to satisfy the bug differently — or that simply didn't
yet define the exact symbol the gold test references — would show `vet FAIL`. That
failure would be about *our* test's expectations, not about the candidate's code
quality. The gate would be measuring the wrong thing.

**The fix.** Run the gates on the candidate's tree *before* the gold test is
installed. Now:

- the gates (step 3) judge the **candidate's code** in isolation, and
- `resolved` (step 5) judges the **outcome** once the instrument is in place.

Clean separation: "is the code good?" and "did it work?" are answered by
different steps and never conflated.

---

<a name="4-status"></a>
## 4. The `status` + tri-state model, and the story of why it exists

This is the heart of Stage 2's design, and it is worth telling *how* it came to be,
because the final shape was reached by fixing two real problems.

### 4.1 The four statuses

Every scored row carries one of four `status` values:

| status | meaning | build/vet/fmt | recall / precision | resolved |
|---|---|---|---|---|
| `resolved` | candidate applied; the failing test now passes and the guard holds | real (should all be `ok`) | real | `true` |
| `unresolved` | candidate applied; tests still fail — *it ran and produced wrong code* | **real** — shows *how* it broke | real | `false` |
| `noop` | empty candidate — *nothing was applied* | **`n/a`** | **`n/a`** | `false` |
| `apply_failed` | a non-empty diff that wouldn't apply to the tree | **`n/a`** (tree unchanged) | real, *from the diff's stated intent* | `false` |

### 4.2 The tri-state gates

Each of `build_ok` / `vet_ok` / `fmt_ok` is not a boolean but **three-valued**:

| value | rendered | means |
|---|---|---|
| `True` | `ok` | the gate ran and the candidate's code passed it |
| `False` | `FAIL` | the gate ran and the candidate's code failed it |
| `None` | `n/a` | **there was nothing to judge** (no candidate code applied) |

`n/a` is a deliberate *third* state. It is never confused with `ok` (judged and
clean) or `FAIL` (judged and broken). It means "not applicable — nothing ran."

### 4.3 Why this exists — the design evolution

The model did not start this rich. It earned each piece by fixing a concrete
misleading behaviour:

**Problem 1 — the `fmt` gate scanned the whole tree.** The first version ran
`gofmt -l .` over the *entire* checkout. validator ships some of its own
`translations/*` and generated files unformatted, so `fmt` came back `FAIL` even
for the gold fix — flagging the *project's* pre-existing formatting, not the
candidate's. **Fix:** scope the `fmt` gate to *only the `.go` files the candidate
changed*. Now `fmt` speaks about the candidate, nothing else.

**Problem 2 — a single pass/fail boolean conflated "wrong code ran" with "nothing
ran."** Originally an empty candidate just showed `resolved = false` with the
gates showing whatever the unchanged tree produced (often a misleading `vet = ok`,
as if something had been judged). That reads as "we evaluated a candidate and it
was clean but didn't resolve" — but *nothing was evaluated at all*. This was
called out directly as misleading: the ruler must distinguish **"the candidate
wrote wrong code"** from **"the candidate produced nothing."** **Fix:** introduce
the explicit `status` field and the `n/a` third state. An empty candidate is now
`noop` with every gate `n/a`; a candidate that applied but failed its tests is
`unresolved` with **real** gates that pinpoint *how* it broke (didn't compile? →
`build FAIL`; built but logic wrong? → `build ok, vet ok`, tests still red).

**Problem 3 — a malformed diff looked like a code failure.** A non-empty diff that
won't even apply is a different failure from one that applies but is wrong. **Fix:**
the `apply_failed` status — gates `n/a` (the tree never changed), but localization
recall/precision are still computed from the diff's *stated intent* (the files it
*names*, even though it didn't apply), because that information is still
meaningful.

The payoff arrives in Stage 4: when the agent misses, one glance at the row says
which kind of miss it was — wrong code (`unresolved`, gates show the flaw),
nothing produced (`noop`), or a malformed patch (`apply_failed`). That is
diagnosis, not just a score.

---

<a name="5-resolved"></a>
## 5. Why `resolved` is tests-only (build dropped from the verdict)

An earlier version computed `resolved = ftp_passed and ptp_passed and build_ok`.
We deliberately **dropped `build_ok` from the verdict**. The reason is that it is
redundant *and* it muddies the roles:

- If the candidate doesn't compile, the tests can't run and therefore fail anyway —
  so `ftp_passed` is already `false`. Adding `and build_ok` changes no outcome.
- But including it blurs the clean split from §3/§4: gates are **diagnostics**
  (they explain *why*), and the tests are the **verdict** (they decide *whether*).

So the final rule is:

```python
resolved = ftp_passed and ptp_passed
```

`build_ok` / `vet_ok` / `fmt_ok` remain on the row purely to *explain* an
`unresolved`: a row showing `build FAIL` tells you it didn't compile; a row showing
`build ok, vet ok` but `resolved = false` tells you it compiled and was clean but
the logic was simply wrong. The verdict stays a pure, test-based measurement —
exactly mirroring the Stage-1 gate (fail→pass→no-regress).

---

<a name="6-metrics"></a>
## 6. The metrics, mapped to the grader's axes

| metric | grader axis it mirrors | precise meaning |
|---|---|---|
| **status** | (diagnostic framing) | which of four outcomes this is: `resolved` / `unresolved` / `noop` / `apply_failed` (§4) |
| **resolved** | "produces a working fix" | the headline (pass@1): `FAIL_TO_PASS` passes **and** `PASS_TO_PASS` still passes (§5) |
| **localization recall** | "identifies the right files" | of the files the human changed, the fraction the candidate also touched |
| **localization precision** | (didn't touch the wrong files) | of the files the candidate touched, the fraction that were correct |
| **build_ok / vet_ok / fmt_ok** | "follows conventions" + "runs validation" | tri-state diagnostics: `ok` / `FAIL` / `n/a` (§4.2) |
| **diff_similarity** | weak hint toward "relevant changes" | a 0–1 textual similarity to the gold diff — **secondary**, reported but never optimized for or gated on |

A few exactness notes:

- `resolved` is decided purely by the tests (`FAIL_TO_PASS` + `PASS_TO_PASS`).
  Gates are diagnostics, not part of the verdict (§5).
- localization precision/recall are `n/a` for a `noop` (no attempt was made), but
  **real** for `apply_failed` (the diff still *names* the files it intended to
  touch, which is a real localization signal even though the patch didn't apply).
- `diff_similarity` is deliberately demoted. A correct fix can look nothing like
  the gold one (different variable names, different valid approach), so similarity
  is reported for interest but never ranked or gated on. This mirrors the
  assignment's own "diff similarity is secondary" framing.

---

<a name="7-calibration"></a>
## 7. Calibration — why we score the gold and empty patches

This answers "why check `resolved` on ground truth — we already *know* the gold
works?" The answer: we are not measuring the fixes, **we are testing the ruler**,
using fixes whose correct score we already know — exactly like dunking a
thermometer in ice water and boiling water before trusting it on a patient.

| input we feed the ruler | correct answer we already know | what a disagreement would reveal |
|---|---|---|
| the **gold** patch | `resolved = yes`, `recall = 1.0` | the harness has a bug (wrong test name, patch didn't apply, sandbox misconfigured, PASS_TO_PASS over-strict…) |
| an **empty** patch | `resolved = no` | the harness is handing out free passes |

If the ruler gets *both* right on all five instances, we trust it to score the
agent's *unknown* patches later. After this one-time calibration, the gold-on-gold
check never matters again — it has done its job of proving the instrument.

---

<a name="8-files"></a>
## 8. The files and how they fit together

| file | role | needs Docker? |
|---|---|---|
| `eval/metrics.py` | pure scoring math: diff→files, recall/precision, similarity, the `InstanceScore` record, the results table | no — unit-testable on its own |
| `eval/run_eval.py` | the orchestrator: checks out, applies the candidate, runs gates, installs the gold test, runs `FAIL_TO_PASS`+`PASS_TO_PASS`, classifies status, prints the table | yes |
| `tests/test_metrics.py` | unit tests for the scoring math (11 tests) | no |
| `src/go_issue_agent/sandbox/runner.py` | Stage-0 sandbox runner; gained one optional arg `extra_mounts` (for the module cache) | — |
| `eval/results/baseline.json`, `gate2.json` | written when you run the harness; the gold scores are the regression baseline | — |

**Separation of concerns.** `metrics.py` is **pure** (no git, no Docker, no files)
so its math can be tested in milliseconds; `run_eval.py` does all the messy I/O
(checkout, apply, sandbox runs). This keeps the trustworthy scoring core verifiable
without a sandbox — the same "small pure core, messy edges isolated" discipline as
Stage 1's scripts.

---

<a name="9-metrics-py"></a>
## 9. `metrics.py` — the pure scoring math, in depth

Five pieces:

**`files_in_diff(diff_text) -> set[str]`.** Reads a unified diff and returns the
set of file paths it touches, by collecting the `+++ b/<path>` headers (and
handling `/dev/null` headers for brand-new files). This is the same diff-reading
idea as `build_ptp.sh`'s package extraction (§5 of stage-1.md), used here for
localization. Runs in O(D) over the diff length.

**`localization(cand_files, gold_files) -> (recall, precision)`.**

```python
hits = len(cand & gold)
recall    = 1.0 if not gold else hits / len(gold)     # of the files the human changed, how many did we hit?
precision = 1.0 if not cand else hits / len(cand)     # of the files we touched, how many were right?
```

Recall asks "did we find the right files?"; precision asks "did we avoid touching
the wrong ones?". For the multi-file bug 1423 (4 files) this is where touching
only `validator.go` would score recall 0.25.

**`diff_similarity(cand, gold) -> float`.** Strips the diff machinery (headers,
`@@` hunks) down to the added/removed *content* lines and compares them with
Python's `difflib.SequenceMatcher`, giving a 0–1 ratio. Secondary signal only.

**`InstanceScore` (a dataclass).** The result record for one scored row. Its
fields encode the tri-state model directly:

| field | type | note |
|---|---|---|
| `instance_id` | str | which bug |
| `candidate` | str | `gold` / `empty` / `agent` |
| `status` | str | `resolved` / `unresolved` / `noop` / `apply_failed` (§4.1) |
| `resolved` | bool | the verdict (§5) |
| `ftp_passed` | bool | did `FAIL_TO_PASS` pass |
| `ptp_passed` | bool | did `PASS_TO_PASS` still pass |
| `recall`, `precision` | `Optional[float]` | `None` ⇒ rendered `n/a` (used for `noop`) |
| `build_ok`, `vet_ok`, `fmt_ok` | `Optional[bool]` | `None` ⇒ `n/a`, `True` ⇒ `ok`, `False` ⇒ `FAIL` (§4.2) |
| `diff_similarity` | float | secondary |

The use of `Optional` is the mechanism behind the `n/a` third state: a field that
was never evaluated is `None`, and the table renderer turns `None` into `n/a`
rather than guessing `ok`/`FAIL` or `0.00`.

**`resolution_rate`, `status_counts`, `format_table`.** `resolution_rate` is the
headline `resolved / total`. `status_counts` tallies how many rows landed in each
of the four statuses (so a run summary can say "5 resolved, 0 unresolved, 5 noop").
`format_table` is the fixed-width printer; it renders `Optional` fields as
`ok`/`FAIL`/`n/a` and floats to two decimals or `n/a`.

These are covered by `tests/test_metrics.py` (11 tests: file extraction, the
recall/precision edge cases incl. empty sets, similarity bounds, the rate,
`status_counts`, and the `n/a` table rendering). They pass with no sandbox.

---

<a name="10-run-eval"></a>
## 10. `run_eval.py` — the orchestrator, in depth (three branches)

It reuses Stage 0's `run_in_sandbox(...)` and Stage 1's checkout/apply logic, so
nothing is reinvented. The important functions:

| function | what it does |
|---|---|
| `load_instance_dirs(only)` | finds instances by globbing `eval/tasks/validator-*/instance.json`; `--only` filters by id |
| `_install_gold_test(dir, inst, id)` | Flavor B: copy `repro_test.go` in and read its test names; Flavor A: apply `test_patch` and use the JSON's `FAIL_TO_PASS`. (A `repro_test.go` always wins — same rule as `verify_gt.sh`.) Returns the `FAIL_TO_PASS` names. |
| `_apply_patch(text)` | tolerant apply (`git apply --ignore-whitespace`, fallback `patch --fuzz=3`); returns a flag distinguishing *empty* (nothing to apply) from *applied* from *failed-to-apply* |
| `_run(cmd)` | run one command in the sandbox with the Go module cache mounted (§12) |
| `_tests_pass(names)` | build `^(name1|name2|…)$`, run `go test -run '<that>' ./...`; pass = exit 0 |
| `_run_resolved(inst)` | run **both** `FAIL_TO_PASS` and `PASS_TO_PASS` and return `(ftp_passed, ptp_passed)` (§11) |
| `evaluate(dir, candidate)` | the per-instance pipeline of §2; branches three ways (below); returns an `InstanceScore` |
| `cmd_gate` / `cmd_candidate` | CLI entry points; `cmd_gate` runs gold + empty and applies the gate-2 verdict |

### 10.1 The three branches of `evaluate`

`evaluate` does not treat every candidate the same. After checking out base and
attempting to apply the candidate (step 2), it branches on what happened:

```
                 ┌─ candidate diff is EMPTY ───────────────► NOOP branch
apply candidate ─┤
                 ├─ non-empty but WON'T APPLY ─────────────► APPLY_FAILED branch
                 └─ applied cleanly ───────────────────────► APPLIED branch
```

**NOOP branch (empty candidate).** Nothing was applied, so there is no candidate
code to judge: `build_ok = vet_ok = fmt_ok = None` (→ `n/a`) and
`recall = precision = None`. We still install the gold test and run it, to record
the true measurement that the failing test *still fails* with no change →
`resolved = False`, `status = "noop"`.

**APPLY_FAILED branch (non-empty diff that won't apply).** The tree never changed,
so the gates are `n/a` again. But localization *is* computed — from the files the
diff *names* (its stated intent) — because "it tried to touch the right files but
the patch was malformed" is a meaningful signal. `resolved = False`,
`status = "apply_failed"`.

**APPLIED branch (the normal path).** The candidate changed the tree. Now, in this
exact order:

```python
repo_ops.checkout(REPO_DIR, inst["base_commit"])      # step 1
applied = _apply_patch(cand_diff)                     # step 2  (branch decided here)
# --- APPLIED branch ---
build_ok = _run("go build ./...").ok                  # step 3: gates judge the CANDIDATE
vet_ok   = _run("go vet ./...").ok                    #         (gold test NOT installed yet)
fmt_ok   = _gofmt_changed_only(cand_files)            #         scoped to candidate's files (§4.3)
ftp_names = _install_gold_test(inst_dir, inst, iid)   # step 4: install the instrument
ftp_passed, ptp_passed = _run_resolved(inst)          # step 5: FAIL_TO_PASS and PASS_TO_PASS
resolved = ftp_passed and ptp_passed                  #         verdict is tests-only (§5)
recall, precision = metrics.localization(cand_files, gold_files)   # step 6
status = "resolved" if resolved else "unresolved"
repo_ops.checkout(REPO_DIR, inst["base_commit"])      # cleanup
```

The `fmt` gate calls `gofmt -l` on **only** the `.go` files the candidate changed
(via `_gofmt_changed_only`), never the whole tree — the fix for Problem 1 in §4.3.

### 10.2 Why this structure

The branch decision lives at exactly one place (right after apply), so the three
very different outcomes can never be confused, and each produces a row whose `n/a`
vs real values are *correct by construction* rather than by accident. This is the
code that makes the §4 model real.

---

<a name="11-ptp"></a>
## 11. How `PASS_TO_PASS` is enforced (and proven enforced)

The verdict requires the regression guard to hold, not just the bug test:

```python
def _run_resolved(inst):
    ftp_passed = _tests_pass(inst["FAIL_TO_PASS"])
    ptp = inst.get("PASS_TO_PASS", [])
    ptp_passed = True if not ptp else _tests_pass(ptp)
    return ftp_passed, ptp_passed
# resolved = ftp_passed and ptp_passed
```

So a candidate that fixes the bug but breaks a regression test gets
`ftp_passed = True`, `ptp_passed = False`, and therefore `resolved = False`,
`status = "unresolved"` — caught exactly as intended.

**This is proven, not assumed.** Stage 1's `scripts/probe_ptp.py` (documented in
stage-1.md §14) stubs the sandbox layer and demonstrates three facts about this
very code: (1) `run_eval` *issues* a separate `PASS_TO_PASS` test run (e.g. 263
names for 1444), (2) when everything passes the result is `resolved=True`, and
(3) when the `PASS_TO_PASS` run is forced to fail, the verdict flips to
`unresolved`/`resolved=False`. Without that probe, "the harness enforces the
guard" would be a claim from reading the source; with it, it is demonstrated.

Run it yourself: `python scripts/probe_ptp.py eval/tasks/validator-1444` (use the
project's `python`, which has `python-dotenv`, not a bare `python3`).

---

<a name="12-cache"></a>
## 12. The persistent module cache (why runs get fast)

In Stage 1 you saw `go: downloading …` before every run — Go re-fetching the
project's dependencies each time, tens of seconds wasted. Stage 2 fixes this by
mounting a host folder, `.cache/gomod`, into the container at `/go/pkg/mod` (Go's
module-cache location). The first run fills it once; every later run reuses it and
skips the downloads. Because `PASS_TO_PASS` runs hundreds of tests across multiple
instances, this cache is what keeps a full `--gate` run practical.

This required one tiny, backward-compatible addition to the Stage-0 sandbox
runner: an optional `extra_mounts` argument (a list of `(host_dir, container_dir)`
pairs). Nothing else about the runner changed, so Stage 0's `gate-0` is unaffected.

---

<a name="13-gate"></a>
## 13. The gate (`gate-2`) — the ruler grading itself

```
python eval/run_eval.py --gate
```

The verdict is the calibration of §7, applied to all five instances:

| condition | requirement |
|---|---|
| gold candidates | **all five** `resolved`, and `recall = 1.0` for each |
| empty candidates | **none** resolved (all `noop`) |

If both hold, it prints `PASSED: gate-2` and writes `eval/results/gate2.json`. If
not, it prints which rows failed and why. The gold run can also be saved as
`eval/results/baseline.json` (via `--candidate gold`) — the regression baseline
future stages compare against.

**Expected behaviour per instance at the gate:**

| id | gold status | gold resolved | empty status | note |
|----|-------------|---------------|--------------|------|
| 1314 | resolved | true | noop | repro flavor; 242 PTP hold |
| 1476 | resolved | true | noop | repro flavor; 271 PTP hold |
| 1444 | resolved | true | noop | `TestUrl` flips; 263 PTP hold |
| 1423 | resolved | true | noop | gold test *panics* unfixed; 243 PTP hold |
| 1284 | resolved | true | noop | gold test won't *compile* unfixed; 265 PTP hold |

Every empty candidate is `noop` with `build/vet/fmt = n/a` and
`recall/precision = n/a` — the harness reports plainly that nothing was applied,
rather than emitting a misleading `ok`/`FAIL`. Its `resolved = false` is the real
measurement: the failing test still fails when no change is made.

**The actual gate run** (real Docker, repeated) produced exactly this: gold 5/5
`resolved` with recall `1.00` and `build/vet/fmt = ok`; empty 5/5 `noop` with
`n/a` everywhere; final line `PASSED: gate-2`. We then locked the tag.

---

<a name="14-run"></a>
## 14. How to run it, and how to read the table

```bash
python eval/run_eval.py --gate                  # the self-check (gold vs empty) — the gate
python eval/run_eval.py --candidate gold        # score gold only, write baseline.json
python eval/run_eval.py --candidate empty       # score empty only
python eval/run_eval.py --only 1314 1284        # restrict to some ids
```

(Use the project's `python`, the activated env with `python-dotenv`; a bare
`python3` will raise `ModuleNotFoundError: dotenv`.)

**Reading a row of the table:**

| column | meaning | want (gold) | want (empty) |
|---|---|---|---|
| `instance` | bug id | — | — |
| `cand` | which candidate | `gold` | `empty` |
| `status` | outcome (resolved / unresolved / noop / apply_failed) | `resolved` | `noop` |
| `recall` | right files found (or `n/a`) | `1.00` | `n/a` |
| `prec` | wrong files avoided (or `n/a`) | `1.00` | `n/a` |
| `build` / `vet` / `fmt` | gates (`ok` / `FAIL` / `n/a`) | `ok` | `n/a` |
| `diff~` | similarity to gold (secondary) | `1.00` | `n/a` |

The first run is slow once (it fills the module cache); subsequent runs are fast.

---

<a name="15-worked"></a>
## 15. Worked examples: reading unresolved / apply_failed rows

The gold/empty rows are the calibration. But the model earns its keep on the
*agent's* future rows, so here is how to read the other two statuses. (These are
illustrative rows of the kind Stage 4 will produce.)

**An `unresolved` row where the code didn't compile:**

```
instance        cand    status      recall  prec   build  vet   fmt   diff~
validator-1284  agent   unresolved  1.00    1.00   FAIL   n/a   ok    0.40
```

Reading it: the agent touched the right file (`recall 1.00`) and only that file
(`prec 1.00`), it's formatted (`fmt ok`) — but `build FAIL` means it didn't
compile, so `vet` couldn't even run (`n/a`) and the tests necessarily failed →
`unresolved`. Diagnosis in one line: *right place, broken code.*

**An `unresolved` row where it compiled but the logic was wrong:**

```
validator-1476  agent   unresolved  1.00    1.00   ok     ok    ok    0.72
```

Everything is clean — it compiled, vetted, formatted, touched the right file — but
the failing test still fails → `unresolved`. Diagnosis: *clean code, wrong fix*
(e.g. a regex that still admits `+0…`). This is the row where you go read the
agent's diff, because the gates can't tell you more.

**An `apply_failed` row:**

```
validator-1423  agent   apply_failed 0.50   1.00   n/a    n/a   n/a   0.10
```

The diff named two of the four real files (`recall 0.50`) and named nothing wrong
(`prec 1.00`), but it wouldn't apply to the tree, so nothing was built or tested
(gates `n/a`) → `apply_failed`. Diagnosis: *malformed patch* — the agent's diff
needs repair before its logic can even be judged.

In every case the combination of `status` + tri-state gates tells you *which kind*
of failure you're looking at before you read a single line of the agent's code.
That is the entire point of the model.

---

<a name="16-gotchas"></a>
## 16. Gotchas we hit and fixed

| symptom | root cause | fix |
|---|---|---|
| gold fix showed `fmt FAIL` | the `fmt` gate ran `gofmt -l .` over the whole tree, flagging validator's *own* unformatted `translations/*` and generated files | scope `fmt` to **only the candidate's changed `.go` files** (§4.3) |
| empty candidate showed a misleading `vet = ok` | a single boolean reported the unchanged tree's gate result as if a candidate had been judged | introduced `status` + the `n/a` third state so "nothing ran" is shown as `noop` + `n/a`, never `ok`/`FAIL` (§4.3) |
| `build_ok` quietly changed the verdict | `resolved` included `and build_ok`, conflating diagnostics with the verdict | dropped it: `resolved = ftp_passed and ptp_passed`; gates are diagnostics only (§5) |
| a vet failure from the *gold test's* new API looked like a candidate flaw | gates ran *after* the gold test was installed | reordered: gates run on the candidate **before** the gold test (§3) |
| "is `PASS_TO_PASS` even being run?" was unprovable by inspection | it's separate code in `run_eval` | wrote `probe_ptp.py` to stub the sandbox and prove the run is issued and flips the verdict (§11) |
| `ModuleNotFoundError: dotenv` | ran with a bare `python3` lacking the project's deps | run with the project's `python` (the activated env) |
| browser saved repeated downloads as `name (1).tar.gz` | re-downloading the same artifact | always extract the **newest by timestamp/size** (you're told the expected byte size each time), and extract **in place** from the repo root |

---

<a name="17-scope"></a>
## 17. What this is NOT (scope boundaries)

- **No agent, no LLM.** Stage 2 only *measures*. It never writes a fix.
- **No tree-sitter / indexing / PageRank / repo-map.** Those are the agent's
  code-intelligence tools and belong to **Stage 3**. The examiner doesn't need the
  agent's eyes to grade.
- **No changes to Stage 1's answer key.** The harness only *reads* the instances;
  `gate-gt` is untouched. (Stage 2 *did* rely on Stage 1's `PASS_TO_PASS` being
  populated — that work lives in Stage 1.)
- **Not a similarity contest.** `diff_similarity` is reported but never gated or
  ranked on; a correct fix may look nothing like the gold one.

---

<a name="18-unlocks"></a>
## 18. What Stage 2 unlocks

- **A trustworthy, automated scorer for any patch** — the instrument every later
  stage reports through, proven honest by the gold/empty calibration and by
  `probe_ptp.py`.
- **A four-way diagnosis, not just a score.** `status` + tri-state gates mean that
  when the agent misses, we instantly know whether it wrote wrong code
  (`unresolved`, gates pinpoint the flaw), produced nothing (`noop`), or emitted a
  malformed patch (`apply_failed`).
- **A saved baseline** (`baseline.json`) that acts as a regression alarm: if a
  future change makes a gold patch stop resolving, we know immediately.
- **The green light to build the agent's machinery** (Stage 3: tree-sitter
  parsing, reference graph, PageRank, token-budget repo-map skeleton) *knowing* we
  can measure whether each piece actually helps.

---

*Stage 3 (only with your go-ahead) is the agent's tools — the indexing stack:
tree-sitter parsing, the reference graph, PageRank ranking, and the token-budget
repo-map skeleton — each unit-tested on a fixture so a tool bug can never
masquerade as a model failure. As with every stage, I'll lay out the what/why/how
plan and wait for your sign-off before building. If any row, function, or worked
example above is unclear, name it and I'll expand that one spot.*
