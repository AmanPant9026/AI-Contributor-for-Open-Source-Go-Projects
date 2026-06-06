# Stage 2 — The Ruler (Eval Harness): Detailed Reference

> **Status:** built; locks at git tag `gate-2` once its self-check is green.
> **One-line summary:** a single command that takes *any* code patch and scores
> it against the frozen Stage-1 answer key on the same axes the assignment grades
> on — and we prove the ruler is trustworthy before ever pointing it at the agent.

This is the long-form reference, written to the same depth as `stage-1.md`.

---

## Table of contents

1. [Why this stage exists](#1-why)
2. [What it does, step by step](#2-what)
3. [The metrics, mapped to the grader's axes](#3-metrics)
4. [Why we score the gold and empty patches (calibration)](#4-calibration)
5. [The files and how they fit together](#5-files)
6. [`metrics.py` — the pure scoring math, explained](#6-metrics-py)
7. [`run_eval.py` — the orchestrator, explained](#7-run-eval)
8. [The persistent module cache (why runs get fast)](#8-cache)
9. [The gate (`gate-2`) — the ruler grading itself](#9-gate)
10. [How to run it, and how to read the table](#10-run)
11. [What this is NOT (scope boundaries)](#11-scope)
12. [What Stage 2 unlocks](#12-unlocks)

---

<a name="1-why"></a>
## 1. Why this stage exists

Stage 1 produced an **answer key**: five bugs, each with a base commit, a gold
fix, and a test proven to fail-when-broken / pass-when-fixed. But the only thing
that knows how to *use* that key so far is `verify_gt.sh`, and it can only check
**one** patch — the gold one. It answers "is this instance valid?", not "how good
is an arbitrary fix?".

When the agent starts producing fixes (Stage 4), "is its fix good?" must become a
**mechanical number**, not a judgment call. Stage 2 builds that measuring
instrument: a scorer that accepts *any* patch and reports a row of numbers. We
build it now, while the only patches in existence are gold and empty, precisely
so we can **prove the instrument is honest** before there's an agent whose output
it will judge.

> Ruler before the thing it measures. The eval harness is the agent's
> *examiner*; it is intentionally built before the agent's *eyes and hands* (the
> tree-sitter indexing stack, which is Stage 3).

---

<a name="2-what"></a>
## 2. What it does, step by step

For each instance, and for a given **candidate** patch (gold / empty / later the
agent's), the harness performs the Stage-1 fail→pass dance, generalized:

| step | action | why |
|---|---|---|
| 1 | check out the repo at `base_commit` (a throwaway copy under `.cache/`) | start from the broken version |
| 2 | apply the **candidate** code patch (gold / empty / later the agent's) | the thing being scored |
| 3 | run the code-quality gates `go build` / `go vet` / `gofmt` — **before** the gold test is installed | judge the candidate's own code, against the project's real tests |
| 4 | install the bug's **gold test** — `repro_test.go` if present, else `test.patch` | the measuring instrument |
| 5 | run the `FAIL_TO_PASS` (and `PASS_TO_PASS`) tests → `resolved` | judge the outcome (does it satisfy the expected behaviour) |
| 6 | turn the results into a score row, then reset the checkout | the measurement + clean slate |

The only difference between candidates is **step 2**: the gold candidate applies
the real fix, the empty candidate applies nothing, the agent candidate (later)
applies whatever the agent produced. Everything else is identical — which is what
makes the comparison fair.

**Why the gates run before the gold test (step 3 before step 4):** the gates
(`build`/`vet`/`fmt`) are meant to judge the *candidate's code quality* — "would
this change pass the project's CI?" If we installed our hidden gold test first
and *then* vetted, a perfectly clean fix that happened not to match the gold
test's exact API would show `vet FAIL`, conflating code quality with
gold-test-satisfaction. By gating first, the gates judge the code and `resolved`
(step 5) judges the outcome — clean separation.

---

<a name="3-metrics"></a>
## 3. The metrics, mapped to the grader's axes

| metric | grader axis it mirrors | precise meaning |
|---|---|---|
| **status** | (diagnostic framing) | which of four outcomes this is: `resolved` / `unresolved` / `noop` / `apply_failed` (see below) |
| **resolved** | "produces relevant code changes" | the headline (pass@1): the `FAIL_TO_PASS` tests pass **and** any `PASS_TO_PASS` still pass |
| **localization recall** | "identifies the right files" | of the files the human changed, the fraction the candidate also touched |
| **localization precision** | (didn't touch the wrong files) | of the files the candidate touched, the fraction that were correct |
| **build_ok / vet_ok / fmt_ok** | "follows conventions" + "runs validation" | tri-state diagnostics: `ok` / `FAIL` / `n/a` |
| **diff_similarity** | hint toward "relevant changes" | a 0–1 textual similarity to the gold diff — **secondary**, reported but never optimized for |

**Why `status` and tri-state gates exist.** A single pass/fail boolean conflates
two very different situations: "a patch ran but was wrong" versus "no patch ran
at all." We separate them:

| status | meaning | build/vet/fmt | recall/precision | resolved |
|---|---|---|---|---|
| `resolved` | applied; the failing test now passes | real (should all be `ok`) | real | true |
| `unresolved` | applied; test still fails — *it ran and produced wrong code* | **real** — shows *how* it broke (didn't compile? vet-dirty? or built fine but logic wrong) | real | false |
| `noop` | empty candidate — *nothing executed* | **`n/a`** (no candidate code to judge) | **`n/a`** (no localization attempt) | false |
| `apply_failed` | a non-empty diff that wouldn't apply | **`n/a`** (tree unchanged) | real *from the diff's intent* | false |

So `n/a` is a deliberate third state meaning "there was nothing to evaluate here"
— never confused with `ok` (judged and clean) or `FAIL` (judged and broken). When
the agent arrives in Stage 4, this is what lets us tell at a glance whether a
miss was "it wrote wrong code" (`unresolved`, with the gates pinpointing the
flaw), "it produced nothing" (`noop`), or "its diff was malformed"
(`apply_failed`).

A few exactness notes:

- `resolved` is decided purely by the tests (`FAIL_TO_PASS` + `PASS_TO_PASS`). If
  code doesn't compile, the tests fail anyway — and the `build_ok` gate then tells
  you *that* was the reason. Gates are diagnostics, not part of the verdict.
- localization precision/recall are `n/a` for a `noop` (no attempt), but real for
  `apply_failed` (the diff still names the files it intended to touch).
- `diff_similarity` is deliberately demoted. A correct fix can look nothing like
  the gold one, so we never gate or rank on it.

---

<a name="4-calibration"></a>
## 4. Why we score the gold and empty patches (calibration)

This is the question "why check `resolved` on ground truth — we already know the
gold works?" The answer: we're not measuring the fixes, **we're testing the
ruler**, using fixes whose correct score we already know — exactly like dunking a
thermometer in ice water and boiling water before trusting it on a patient.

| input we feed the ruler | correct answer we already know | what it would mean if the ruler disagreed |
|---|---|---|
| the **gold** patch | resolved = yes, recall = 1.0 | the harness has a bug (wrong test name, patch didn't apply, sandbox misconfigured…) |
| an **empty** patch | resolved = no | the harness is handing out free passes |

If the ruler gets *both* right on all five instances, we trust it to score the
agent's unknown patches later. After this one-time calibration, the gold-on-gold
check never matters again.

---

<a name="5-files"></a>
## 5. The files and how they fit together

| file | role | needs Docker? |
|---|---|---|
| `eval/metrics.py` | pure scoring math: diff→files, recall/precision, similarity, the results table | no — unit-testable on its own |
| `eval/run_eval.py` | the orchestrator: checks out, installs the test, applies the candidate, runs gates+tests in the sandbox, calls `metrics.py`, prints the table | yes |
| `tests/test_metrics.py` | unit tests for the scoring math | no |
| `src/go_issue_agent/sandbox/runner.py` | Stage-0 sandbox runner; gained one optional arg `extra_mounts` (for the module cache) | — |
| `eval/results/baseline.json`, `gate2.json` | written when you run the harness; the gold scores are the regression baseline | — |

Separation of concerns: `metrics.py` is **pure** (no git, no Docker, no files) so
its math can be tested in milliseconds; `run_eval.py` does all the messy
I/O. This keeps the trustworthy core verifiable without a sandbox.

---

<a name="6-metrics-py"></a>
## 6. `metrics.py` — the pure scoring math, explained

Four pieces:

- **`files_in_diff(diff_text)`** — reads a unified diff and returns the set of
  file paths it touches, by scanning the `+++ b/<path>` lines (and `diff --git`
  headers for new files). Used for localization.
- **`localization(cand_files, gold_files)`** — returns `(recall, precision)`:
  ```python
  hits = len(cand & gold)
  recall    = 1.0 if not gold else hits/len(gold)      # found the right files?
  precision = 1.0 if not cand else hits/len(cand)       # avoided wrong files?
  ```
- **`diff_similarity(cand, gold)`** — strips the diff machinery (headers, `@@`)
  down to the added/removed *content* lines and compares with Python's
  `difflib.SequenceMatcher`, giving 0–1. Secondary signal only.
- **`InstanceScore`** (a dataclass) + **`resolution_rate`** + **`format_table`** —
  the result record, the headline rate (`resolved / total`), and the fixed-width
  table printer.

These are covered by `tests/test_metrics.py` (9 tests: file extraction, the
recall/precision edge cases, similarity bounds, the rate). They pass without any
sandbox.

---

<a name="7-run-eval"></a>
## 7. `run_eval.py` — the orchestrator, explained

It reuses Stage 0's `run_in_sandbox(...)` and Stage 1's checkout/apply logic, so
nothing is reinvented. The important functions:

| function | what it does |
|---|---|
| `load_instance_dirs(only)` | finds instances by globbing `eval/tasks/validator-*/instance.json`; `--only` filters by id |
| `_install_gold_test(dir, inst, id)` | Flavor B: copy `repro_test.go` in and read its test names; Flavor A: apply `test_patch` and use the JSON's `FAIL_TO_PASS`. (A `repro_test.go` always wins — same rule as `verify_gt.sh`.) |
| `_apply_patch(text)` | tolerant apply (`git apply --ignore-whitespace`, fallback `patch --fuzz=3`); an empty patch is a successful no-op |
| `_run(cmd)` | run one command in the sandbox with the Go module cache mounted |
| `_tests_pass(names)` | run `go test -run '^(…)$' ./...`; pass = exit 0 |
| `evaluate(dir, candidate)` | the per-instance pipeline of §2; returns an `InstanceScore` |
| `cmd_gate` / `cmd_candidate` | the CLI entry points; `cmd_gate` runs gold + empty and applies the gate-2 verdict |

The pipeline inside `evaluate` (abridged):

```python
repo_ops.checkout(REPO_DIR, inst["base_commit"])      # step 1
_apply_patch(cand_diff)                               # step 2 (no-op if empty)
build_ok = _run("go build ./...").ok                  # step 3: gates judge the candidate
vet_ok   = _run("go vet ./...").ok                    #         (gold test NOT installed yet)
fmt_ok   = <gofmt only the candidate's changed .go files>
ftp = _install_gold_test(inst_dir, inst, iid)         # step 4: install the instrument
ftp_passed = _tests_pass(ftp)                         # step 5: judge the outcome
resolved = ftp_passed and ptp_passed and build_ok
recall, precision = metrics.localization(cand_files, gold_files)  # step 6
repo_ops.checkout(REPO_DIR, inst["base_commit"])      # cleanup
```

The `fmt` gate checks only the `.go` files the candidate changed (via
`gofmt -l <those files>`); scanning the whole tree would wrongly flag the
project's own pre-existing unformatted files.

---

<a name="8-cache"></a>
## 8. The persistent module cache (why runs get fast)

In Stage 1 you saw `go: downloading …` before every run — Go re-fetching the
project's dependencies each time, ~30–60s wasted. Stage 2 fixes this by mounting
a host folder, `.cache/gomod`, into the container at `/go/pkg/mod` (Go's module
cache location). The first run fills it once; every later run reuses it and skips
the downloads.

This required one tiny, backward-compatible addition to the Stage-0 sandbox
runner: an optional `extra_mounts` argument (a list of `(host_dir, container_dir)`
pairs). Nothing else about the runner changed.

---

<a name="9-gate"></a>
## 9. The gate (`gate-2`) — the ruler grading itself

```
python eval/run_eval.py --gate
```

The verdict is the calibration of §4, applied to all five instances:

| condition | requirement |
|---|---|
| gold candidates | **all** resolved, and recall = 1.0 for each |
| empty candidates | **none** resolved |

If all three hold, it prints `PASSED: gate-2` and writes
`eval/results/gate2.json`. If not, it prints which rows failed. The gold run can
also be saved as `eval/results/baseline.json` (via `--candidate gold`) — that's
the regression baseline future stages compare against.

Expected behaviour per instance at the gate:

| id | gold status | empty status | note |
|----|-------------|--------------|------|
| 1314 | resolved | noop | repro flavor |
| 1476 | resolved | noop | repro flavor |
| 1444 | resolved | noop | `TestUrl` flips |
| 1423 | resolved | noop | the gold test *panics* when run unfixed |
| 1284 | resolved | noop | the gold test won't compile unfixed |

Every empty candidate is `noop` with `build/vet/fmt = n/a` and
`recall/precision = n/a` — the harness reports plainly that nothing was applied,
rather than emitting a misleading `ok` or `FAIL`. Their `resolved = false` is the
real measurement (the failing test still fails with no change).

---

<a name="10-run"></a>
## 10. How to run it, and how to read the table

```bash
python eval/run_eval.py --gate                  # the self-check (gold vs empty)
python eval/run_eval.py --candidate gold        # score gold only, write baseline.json
python eval/run_eval.py --candidate empty       # score empty only
python eval/run_eval.py --only 1314 1284        # restrict to some ids
```

Reading a row of the table:

| column | meaning | want (gold) | want (empty) |
|---|---|---|---|
| `instance` | bug id | — | — |
| `cand` | which candidate | `gold` | `empty` |
| `status` | outcome (resolved/unresolved/noop/apply_failed) | `resolved` | `noop` |
| `recall` | right files found | `1.00` | `n/a` |
| `prec` | wrong files avoided | `1.00` | `n/a` |
| `build`/`vet`/`fmt` | gates (ok/FAIL/n/a) | `ok` | `n/a` |
| `diff~` | similarity to gold (secondary) | `1.00` | `0.00` |

The first run is slow once (it fills the module cache); subsequent runs are fast.

---

<a name="11-scope"></a>
## 11. What this is NOT (scope boundaries)

- **No agent, no LLM.** Stage 2 only *measures*. It never writes a fix.
- **No tree-sitter / indexing / PageRank / repo-map.** Those are the agent's
  code-intelligence tools and belong to **Stage 3**. The examiner doesn't need
  the agent's eyes to grade.
- **No changes to Stage 1.** The answer key and `gate-gt` are untouched; the
  harness only *reads* the instances.

---

<a name="12-unlocks"></a>
## 12. What Stage 2 unlocks

- A trustworthy, automated scorer for any patch — the instrument every later
  stage reports through.
- A saved **baseline** (`baseline.json`) that acts as a regression alarm: if a
  future change makes a gold patch stop resolving, we know immediately.
- The green light to build the agent's machinery (Stage 3: tree-sitter parsing,
  reference graph, PageRank, token-budget repo-map skeleton) *knowing* we can
  measure whether each piece actually helps.

---

*Stage 3 (only with your go-ahead) is the agent's tools — the indexing stack you
asked about: tree-sitter parsing, the reference graph, PageRank ranking, and the
token-budget repo-map skeleton — each unit-tested on a fixture so a tool bug can
never masquerade as a model failure.*
