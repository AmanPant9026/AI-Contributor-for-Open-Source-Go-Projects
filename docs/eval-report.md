# Evaluation Report — Agentic Go Issue‑Fixing Agent

This report states **how the agent was evaluated and what it scored**, across all three approved
repositories it was run on. It is the results‑and‑evidence companion to `docs/stage-6.md` (which
carries the narrative and the ceiling analysis); here the focus is methodology, the full
per‑instance results matrix, the mapping to the assignment's stated evaluation criteria, sample
outputs, and the exact commands to reproduce every number.

Two honesty rules govern this document, the same ones used throughout the project:

- **Every figure is a range, not a point.** The reproduction and repair steps are live,
  non‑deterministic model calls; a different run can reproduce an instance this one didn't (see
  §7). "0 on cobra" means "0 across the runs we executed," not a theorem.
- **No number here is fabricated or rounded in our favour.** Each comes from a run whose console
  output is reproducible with §10's commands.

---

## 1. Scope and setup

| Item | Value |
|---|---|
| Repositories evaluated | `go-playground/validator` (development repo), `spf13/cobra`, `gin-gonic/gin` — all from the approved list |
| Reference model | `anthropic/claude-sonnet-4-5` (via litellm); a stronger model (Opus 4.8) is used in the §7 probe |
| Sandbox | Docker image `go-issue-agent-sandbox:dev` (golang:1.24), native arm64 |
| Agent state | **Frozen** across all repos — identical binary, prompts, evidence ladder, repair loop, and thresholds; only the test set changed |
| What "the agent" submits | a **code‑only** patch that passes build + vet + its own reproduction, **or** it abstains |

`validator` is the chosen development repo and the headline gauge; `cobra` and `gin` are held‑out
generalization checks.

---

## 2. How instances were built, and the gold‑validation gate

### 2.1 Instance construction

- **validator (5 instances):** hand‑curated ground truth — real reported bugs, each with the
  fixing PR's gold patch and gold test, and `FAIL_TO_PASS` / `PASS_TO_PASS` sets.
- **cobra / gin (held‑out):** built the **same way**, issue‑first and human‑selected, via
  `eval/make_instance.py <repo> --issue N --pr M`. Selection is by the quality of the *question*
  (a real, clearly‑described, small/medium bug with a test‑bearing fix PR), fixed **before** the
  agent runs — never by whether the agent solves it.

### 2.2 The gold‑validation gate (the firewall)

Every candidate must prove it is a real, reproducible bug in our own sandbox before the agent is
allowed near it (`eval/run_eval.py --validate`): at the base commit the gold **test** must
**fail**; after the gold **fix** it must **pass**; `PASS_TO_PASS` is derived as a regression
guard. Only confirmed **FAIL → PASS** instances are kept. This is why a 0 resolve‑rate is
meaningful — every instance the agent faced was a gold‑solvable bug.

**Validation results (what passed the firewall, and what it rejected):**

| Repo | Validated (kept) | Excluded | Typical exclusion reason |
|---|---|---|---|
| validator | 5 / 5 (ground truth) | 0 | — |
| cobra (curated) | 2 / 2 — `cobra‑1093` (F2P=1, P2P=181), `cobra‑1651` (F2P=2, P2P=216) | 0 | — |
| gin (curated) | 1 / 1 — `gin‑3659` (F2P=1, P2P=431) | 0 | — |
| cobra (earlier harvested batch) | 4 / 15 | 11 | "F2P did not run at base" → the PR was a **new feature**, not a bug |
| gin (earlier harvested batch) | 2 / 12 | 10 | mostly feature PRs; some "F2P passes at base" |

The high exclusion rate on the harvested batches is itself a finding: **small, reproducible bugs
are rare in a mature repo's recent history** — most merged PRs that carry a test are features.
The gate also flagged one curated pick (`gin #2346`) as un‑gaugeable because its fix PR added no
isolable test function; `make_instance` refused to build it. These are the firewall working.

---

## 3. Metrics — definitions

| Metric | Definition |
|---|---|
| **Resolved** | the submitted patch makes all `FAIL_TO_PASS` tests pass **and** keeps `PASS_TO_PASS` passing, with build + vet + gofmt clean |
| **Localization recall / precision** | of the gold‑changed files, how many the agent edited (recall); of the agent's edits, how many were gold‑changed files (precision) |
| **build / vet / fmt** | `go build`, `go vet`, `gofmt` status of the submitted patch |
| **diff~** | textual similarity of the agent's patch to the gold patch (a *proxy*; behaviour is judged by the tests, not this number) |
| **status** | `resolved` · `unresolved` (submitted, tests don't all pass) · `noop` (abstained — nothing submitted) · `error` (crash) |
| **ran clean** | the agent completed without crashing |
| **do‑no‑harm** | the agent submits only a patch that passed its **own** build+vet+reproduction, else abstains |

---

## 4. Full per‑instance results matrix

Reference model (Sonnet), latest run including the Stage‑6 repair‑loop fixes. `gin‑4336` is
reported from the §7 Opus probe (the curated `gin‑3659` gauge is pending an API top‑up and is
marked accordingly).

| Instance | Repo | Status | Recall | Prec | build/vet/fmt | diff~ | Failure mode |
|---|---|---|---|---|---|---|---|
| validator‑1284 | validator | unresolved | 1.00 | 1.00 | ok/ok/ok | 0.27 | fix‑reasoning (wrong‑vs‑gold) |
| validator‑1314 | validator | **resolved** | 1.00 | 1.00 | ok/ok/ok | 0.99 | — |
| validator‑1423 | validator | noop (abstain) | n/a | n/a | n/a | 0.00 | reproduction (no repro) |
| validator‑1444 | validator | unresolved | 1.00 | 1.00 | ok/ok/ok | 0.21 | fix‑reasoning (wrong‑vs‑gold) |
| validator‑1476 | validator | **resolved** | 1.00 | 1.00 | ok/ok/ok | 0.95 | — |
| cobra‑1093 | cobra | noop (abstain) | n/a | n/a | n/a | 0.00 | reproduction (no repro) |
| cobra‑1651 | cobra | noop (abstain) | n/a | n/a | n/a | 0.00 | fix‑reasoning (repro ok, no passing fix) |
| gin‑3659 | gin | *pending* | — | — | — | — | gauge not yet run (credits) |
| gin‑4336 (Opus) | gin | unresolved | 1.00 | 1.00 | ok/ok/ok | 0.34 | fix‑reasoning (wrong‑vs‑gold) |

Earlier harvested held‑out batch (for completeness; same two failure modes):

| Instance | Repo | Status | Failure mode |
|---|---|---|---|
| cobra‑2070 | cobra | noop | reproduction |
| cobra‑2180 | cobra | noop | fix‑reasoning (reproduced, no passing fix) |
| cobra‑2241 | cobra | noop | reproduction |
| cobra‑2356 | cobra | noop | fix‑reasoning (reproduced, no passing fix) |
| gin‑2169 | gin | noop | reproduction |

**The pattern, stated once:** every non‑resolve on every repo is exactly one of two modes —
*couldn't reproduce* or *reproduced but wrote the wrong fix*. Both appear on validator too; see §9.

---

## 5. Aggregate metrics

| Metric | validator | cobra | gin |
|---|---|---|---|
| Resolution rate | **2 / 5** (0.40) | 0 / 2 curated (0 / 4 harvested) | 0 / 1 gauged (gauge of `gin‑3659` pending) |
| Localization recall (when scored) | **1.00** on all 4 scored | right files found (coverage 4–5) | right file found (coverage 10–11) |
| Patches that failed the agent's own checks | **0** | **0** | **0** |
| Wrong‑vs‑gold patches submitted | 2 (`1284`, `1444`) | 0 (abstained) | 1 (`4336`) |
| Ran clean (no crash) | 5 / 5 | 2 / 2 | 1 / 1 (the credit error is an environment stop, not a crash) |

Two aggregate facts worth pulling out:

- **Localization is effectively solved and generalizes:** recall 1.0 on every scored validator
  instance, and coverage‑based localization found the right files on cobra and gin too.
- **The do‑no‑harm contract held everywhere** — the agent never submitted a patch that failed its
  *own* build/vet/reproduction. See §9 for the honest bound on that statement.

---

## 6. Mapping to the assignment's evaluation criteria

The brief states it will compare the agent's changes to accepted PRs on five axes. Evidence for
each:

| Criterion | Evidence | Verdict |
|---|---|---|
| **Identifies the right files** | localization recall **1.00** on all scored validator instances; coverage found the buggy files on cobra and gin | **Strong** |
| **Produces relevant code changes** | resolves contained bugs at diff ~0.95–0.99 (`1314`, `1476`); on harder bugs it edits the right file and produces a *plausible* (if not gold‑matching) change | **Strong on its target class; bounded on subtle‑semantics bugs** |
| **Follows project conventions** | submitted patches are `gofmt`‑clean and pass `go vet`; changes are minimal and localized, in the repo's own style | **Strong** (build/vet/fmt all `ok` on every submission) |
| **Runs appropriate validation** | the core of the design: the agent writes a reproduction, runs it under the Docker sandbox, and submits **only** a build+vet+reproduction‑verified patch — otherwise abstains. A gold‑validation harness independently confirms instances | **Strong — this is the agent's distinguishing strength** |
| **Generates a reasonable PR summary** | the finalize phase emits a PR title + body from the verified change (see §8 sample) | **Present**; demonstrated on resolved instances |

The one axis the brief does **not** list is raw resolution rate — and that is the axis most bound
by the model's reasoning rather than the framework (§9).

---

## 7. The Opus (model) comparison

To attribute the ceiling correctly we re‑ran held‑out instances on a stronger model (Opus 4.8),
everything else frozen:

- **`gin‑4336`:** under Sonnet the agent mislocalized and never found the bug; under **Opus** it
  localized to `recovery.go` and produced a patch that built, vetted, and passed its own
  reproduction **on attempt 1** — but the patch was **wrong‑vs‑gold** (§8). → *Fix‑generation is
  model‑bound, not framework‑bound; the wrong‑vs‑gold ceiling persists even at the frontier.*
- **`cobra‑2180`, `cobra‑2356`:** had reproduced under Sonnet on an earlier run; under Opus on this
  run they **failed to reproduce**. → *The reproduction step is non‑deterministic; a single run is
  never a verdict.*

Implication for scoring more bugs: the biggest lever is **model strength** (costs nothing — swap
`LLM_MODEL`); it widens the solvable class but does not make the agent universal.

---

## 8. Sample outputs

### 8.1 Worked example — `gin‑4336` (verbatim, the wrong‑vs‑gold case)

The agent (Opus) localized correctly and produced a *verified‑against‑its‑own‑test* patch; the
gold‑gate then judged it against the maintainer's test and they diverged. This is the most
instructive sample because it shows both the capability and the ceiling.

**Agent's submitted fix** (`recovery.go`) — re‑panic `ErrAbortHandler`:

```go
if errors.Is(err.(error), http.ErrAbortHandler) {
    panic(err)
}
```

**Agent's reproduction** (its own self‑test) — asserts the panic propagates:

```go
defer func() {
    r := recover()
    if r == nil { t.Fatal("expected http.ErrAbortHandler to be re-panicked") }
    if r != http.ErrAbortHandler { t.Fatalf("expected http.ErrAbortHandler, got %v", r) }
}()
```

**Gold fix** (maintainer's accepted PR) — treat it as a broken pipe (swallow quietly):

```go
if e, ok := err.(error); ok && errors.Is(e, http.ErrAbortHandler) {
    brokenPipe = true
}
```

**Gold test** — asserts the observable contract:

```go
assert.Equal(t, 204, w.Code)
assert.NotContains(t, out, "panic recovered")
```

**Behavioural check:** applying the agent's fix and running the *gold* test yields
`expected 204, got 500`, with `panic recovered` in the log → the fix is genuinely wrong‑vs‑gold.
Both are defensible readings of the bug; the agent's self‑test was too narrow to catch that its
reading differed from the maintainer's. (Full four‑artifact walkthrough: `docs/stage-6.md` §8.)

### 8.2 Resolved‑instance outputs and PR summaries

For the resolved instances (`validator‑1314`, `validator‑1476`) the harness writes the agent's
verified patch and generated PR summary to `eval/results/agent/<instance>.patch` and the run
trace. These are the cleanest positive samples (diff ~0.95–0.99 to gold) and should be attached
to the submission directly from that directory; they are produced by the run in §10 and are not
reproduced here to avoid transcribing machine output by hand.

---

## 9. Honest findings and the competence boundary

The full analysis is in `docs/stage-6.md`; the evaluation‑level summary:

- **The agent reliably resolves one class:** small, contained, well‑described bugs — a
  value/condition/regex/single‑expression fix whose reproduction is small and whose correct form
  the model can reason (`validator‑1314`, `1476`).
- **It does not behave differently on cobra/gin than on validator.** Validator simply contains two
  bugs of that class; its other three instances fail in the *same two modes* the held‑out repos
  do — `validator‑1284`/`1444` are right‑file‑wrong‑fix, `validator‑1423` is no‑repro.
- **Whose limit:** wrong‑vs‑gold is the **model's** fix‑reasoning; no‑repro on rich issues is the
  **model's** test‑writing; no‑repro on one‑line reports is the **input's** (and abstaining is
  correct there). None is a framework defect.
- **The do‑no‑harm contract, stated precisely:** the agent never submits a patch that fails its
  *own* checks. That guarantee is **exactly as strong as its self‑written reproduction** — where
  the reproduction is too weak to distinguish a correct fix from a plausible wrong one, a
  wrong‑vs‑gold patch *does* pass its checks (`1284`, `1444`, `gin‑4336`). We report this rather
  than claim a blanket "never wrong."

---

## 10. Reproducibility

Every number above is reproducible from the repo root on a machine with Docker and a
`GITHUB_TOKEN` (and an Anthropic key in `.env`):

```bash
# 0. unit tests (the pure logic behind the harness)
python -m pytest tests/ -q                      # 95 passed

# 1. (re)build the held-out instances by their chosen issue/PR pairs
python eval/make_instance.py cobra --issue 1093 --pr 1095
python eval/make_instance.py cobra --issue 1651 --pr 1776
python eval/make_instance.py gin   --issue 3659 --pr 3666

# 2. gold-validate every instance (the firewall) — keeps only real FAIL->PASS
python eval/run_eval.py --validate --prefix validator
python eval/run_eval.py --validate --prefix cobra
python eval/run_eval.py --validate --prefix gin

# 3. gauge the frozen agent (reference model from .env)
python eval/run_eval.py --gate4 --prefix validator     # -> resolves 2/5 (1314, 1476)
python eval/run_eval.py --gate4 --prefix cobra
python eval/run_eval.py --gate4 --prefix gin

# 4. the model-comparison probe (stronger model)
LLM_MODEL=anthropic/claude-opus-4-8 python eval/run_eval.py --gate4 --prefix gin --only gin-4336
```

Per‑instance artifacts (patches, traces, PR summaries) are written under `eval/results/agent/`.
Because the reproduction/repair steps are live model calls, re‑runs vary within the ranges
described in §4–§5; the *shape* (the two failure modes, perfect localization, do‑no‑harm) is
stable across runs.
