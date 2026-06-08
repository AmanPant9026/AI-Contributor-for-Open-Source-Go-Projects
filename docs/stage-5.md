# Stage 5 — Reliability, Execution‑Evidence Localization, and Honest Measurement

Stage 5 takes the working-but-limited agent from gate‑4 (clean on all 5 instances,
resolving 2/5 on the reference model) and does three things: hardens it against the
failures a real run hits (flaky APIs, timeouts, one bad instance), replaces prose‑based
localization with **localization from what the code does when you run it**, and adds the
robustness machinery the project plan called for (a result cache and a decision tracer).

The most important number in this document is not a score. It is the honest
characterization of *variance*: on five instances, with a non‑deterministic model and two
borderline cases, resolution **ranges 2–3 of 5 run‑to‑run**, and we can say precisely why.

---

## Table of contents

1. What Stage 5 is, and where it sits
2. Scope — the plan, what we built, what we declined
3. Locked decisions (carried + new)
4. The thesis — localize from execution, not prose
5. Component deep dives
6. File‑by‑file reference
7. Verification philosophy — do‑no‑harm, and the weak‑repro ceiling
8. Results — measured, across runs
9. gate‑5 — definition and evidence
10. Tests
11. Findings — the Stage 5 narrative
12. Limitations & future work
13. How to run / reproduce

---

## 1. What Stage 5 is, and where it sits

gate‑4 proved the architecture: swap a local model for a frontier model and resolution went
from 0 to 2/5 with **zero wrong patches submitted**. It also exposed exactly one wall —
**localization**. `#1476` (the E.164 phone‑code bug) could not be resolved because the file
that needed editing, `regexes.go`, never surfaced from the issue text: the prose says
"phone", "codes", "validation" — it does **not** contain the token `e164`, so lexical
ranking buried the real file at rank ~17 of 40 and the agent edited the wrong place.

Stage 5 attacks that wall and shores up the surrounding machinery. It has two workstreams:

- **Workstream B — reliability hardening.** The unglamorous half: API retry/backoff,
  tightened sandbox timeouts, per‑instance isolation. Mostly already present from Stage 0–2;
  the real gaps were closed and unit‑tested.
- **Workstream A — execution‑evidence localization (the centerpiece).** Run the agent's own
  reproduction test under coverage and use *what executed* — plus any compiler error or panic
  trace — to find the buggy file. This was **not** in the original Stage 5 plan (see §2); it
  is the work that actually moved the score and cracked `#1476`.

Plus the two plan‑mandated robustness features: a **content‑addressed result cache** (a
re‑run makes zero model calls) and a **decision tracer** (a structured record of every tool
call and decision).

---

## 2. Scope — the plan, what we built, what we declined

The original Stage 5 plan read:

> **Stage 5 — Reliability.** Do: bounded repair loop, multi‑candidate + test‑based
> selection/majority vote, artifact cache, parallel candidate validation, decision tracer.
> Gate: inject a broken patch → repair engages and provably stops at N; selection picks a
> validated candidate when one exists; a re‑run hits the cache (zero LLM calls for unchanged
> inputs); the trace lists tool calls.

We held ourselves to that list rather than quietly redefining it. Honest accounting:

| Plan item | Status | Notes |
|---|---|---|
| Bounded repair loop | ✅ **Done** | top‑3 files × 2 attempts, hard cap; unit‑tested for "stops at N" |
| Multi‑candidate selection | ✅ **Done** | walks the coverage‑narrowed, model‑ranked candidates |
| Test‑based selection / **majority vote** | ⛔ **Declined (in writing)** | see below — it fixes neither real failure mode and would worsen `#1284` |
| Artifact cache | ✅ **Done** | content‑addressed; re‑run = zero model calls (proven, §9) |
| Parallel candidate validation | ⏸ **Deferred to Stage 6** | pure speed; only pays off at scale (2nd repo, more instances) |
| Decision tracer | ✅ **Done** | structured per‑instance trace; tool calls + decisions |
| **gate‑5** four checks | ✅ **All green** | §9 |

And the major thing we **added** that the plan did not list:

| Added | Why |
|---|---|
| **Execution‑evidence localization** (coverage ladder + repair rework) | gate‑4 showed localization was *the* wall; this is the only work in Stage 5 that moved resolution (2/5 → 3/5) and resolved `#1476` |

**Why majority‑vote is declined, not built.** Generating N fixes and voting only helps if
the agent produces *several differing plausible fixes* and the vote selects the best. Two
gate runs show our failure modes are not that. They are (a) "couldn't apply an edit to the
believed file" (`#1476`, pre‑rework) and (b) "the agent's own repro is weaker than the gold
test, so a fix passes ours and fails theirs" (`#1284`, `#1444`). Majority‑vote fixes
**neither**. Worse, on `#1284` it is actively harmful: voting among fixes that all pass a
weak repro just selects a wrong one *more confidently*, against do‑no‑harm. It is N× the
model cost and real complexity for **zero expected resolution gain on our set** — the same
overfitting trap we avoided with semantic embeddings in Stage 4. Our existing **first‑verified**
policy *is* the deliberate selection rule, and it satisfies the gate's "selection picks a
validated candidate when one exists." We would revisit voting only if "several plausible
differing fixes, gate cannot separate them" ever became a *real observed* failure. It has not.

**Why parallelism is deferred, not declined.** It is a latency optimization. With five
instances, first‑verified stop, and wrong‑file attempts that fail *cheaply* (apply fails
before Docker), sequential wall‑time is fine. Concurrent sandboxes add orchestration and
memory contention on a 16 GB laptop that only pays off with many instances — i.e. Stage 6.

---

## 3. Locked decisions (carried + new)

Carried from earlier stages: full tool‑using agent; our own tools; Python harness; Go in
Docker; `go-playground/validator` as the target repo; outside‑in build order; **fail‑fast
gating**; git tag at each green gate; and the disallowed prior‑art name is never used in any deliverable.

New in Stage 5:

1. **Localize from execution, not prose** (the evidence ladder, §4).
2. **Coverage NARROWS; the model ORDERS.** Coverage reduces ~40 files to the handful the bug
   touches; lexical ranking is the *wrong* tool to order that shortlist when the buggy file is
   a quiet data/util file, so the model ranks it instead. (See §11 for why we landed here.)
3. **Additive, never replacing.** Every change is a strict superset of gate‑4 behavior. With
   no execution evidence (e.g. a repro that won't compile on base), the pipeline falls back to
   *exactly* the gate‑4 localization and repair path. This is enforced in code and proven by
   the gate‑4 tests passing unchanged.
4. **Embeddings stay dropped.** Measured at zero benefit twice in Stage 4; not reconsidered.
5. **Cache real calls only.** The result cache auto‑disables whenever a fake model is
   injected, so test runs never leak cached values into each other.
6. **Report the range, not a cherry‑picked number** (§8).

---

## 4. The thesis — localize from execution, not prose

A bug report is a description; the bug is a behavior. Stage 4's localizer ranked files by how
well the *issue text* matched the *code text* (lexical hits) and structural centrality
(PageRank). That works when the issue names the code — and fails exactly when it doesn't,
which is `#1476`.

The Stage 5 thesis: **after the agent has written a test that provably reproduces the bug,
run that test and let the execution tell you where the bug lives.** Formalized as an
**evidence ladder** — the strongest available rung wins, and we fall through when a rung is
blank:

```
compiler error file:line     (10)   build fails -> the error names the file directly
panic / runtime trace        (9-8)  repro crashes -> the deepest *repo* frame in the stack
coverage of the repro        (~8)   repro runs -> the ~8 files it actually executed   <-- workhorse
issue identifiers            (7)    lexical hits -> ORDER the survivors
PageRank                     (4)    structural centrality -> tiebreaker
[embedding similarity]        --    DROPPED (measured zero benefit in Stage 4)
```

The top three rungs only fire when the bug **crashes or fails to compile**. Most real bugs
are *silent* — `#1476` compiles, doesn't panic, just returns the wrong boolean. For those,
rungs 10/9/8 are blank, and **coverage** is the rung that carries the day: a silent bug still
*executes* `regexes.go`, even though it never mentions it. That is the gap prose could not
close.

Crucially, coverage does **narrowing**, not ordering. Running the `#1476` repro reduces 40
files to 7 — and `regexes.go` is in the 7. Ordering those 7 is a separate problem, and §11
explains why we hand it to the model rather than to lexical ranking.

---

## 5. Component deep dives

### 5.1 Workstream B — reliability hardening (`llm/client.py`, `tools/go_tools.py`)

About 80% already existed (sandbox timeouts via the Stage‑0 runner; per‑instance isolation
via the harness `try/except → status="error" → continue`). The real gaps:

- **Transient API retry with backoff.** `llm/client.py` wraps each completion in
  `_retry_transient`: a *transient* failure (rate limit, 429, 500/502/503, timeout, dropped
  connection) retries with exponential backoff (1→2→4→8 s, max 4). Non‑transient errors (bad
  key, bad request) raise immediately — no pointless retries. The per‑attempt
  **temperature‑fallback** (some models, e.g. Opus 4.x, reject `temperature`) is preserved
  inside the retried call.
- **Tightened tool timeouts.** `go_tools` build/test 300 s, vet 180 s, fmt 60 s (was a flat
  1200 s), so a hung toolchain fails fast instead of stalling the run.

Unit‑tested in `tests/test_reliability.py` (4 fault‑injection tests with an injected `sleep`,
so they don't actually wait): transient error recovers, non‑transient raises immediately,
backoff schedule is correct, and one instance crashing does not abort the others.

### 5.2 Execution‑evidence localization (`phases/evidence.py`, `tools/go_tools.py`)

**The diagnostic came first.** Before writing any agent code, a throwaway diagnostic
(`eval/diagnose_coverage.py`) ran the five repros under coverage and printed the executed‑file
set. It flipped a wrong prediction (we expected `regexes.go` to be a data‑only file with no
coverage; it has functions and **is** covered) and confirmed the thesis on real data:

| Instance | Coverage narrows to | Buggy file in the set? |
|---|---|---|
| `#1476` | 40 → 8 files | ✅ `regexes.go` (lexical had it at rank 17) |
| `#1314` | logic bug | ✅ `baked_in.go` directly |
| `#1444` | logic bug | ✅ `baked_in.go` directly |
| `#1284` | only `regexes.go` spurious | ❌ gold file not covered — fix *adds* an API that doesn't exist on base, so the repro can't call it → coverage **can't** help → graceful fallback to prose |

`phases/evidence.py` is the productionized, **pure** result (string in, ranking out — fully
unit‑tested without Docker):

- `module_path(repo)` — read the module prefix from `go.mod`.
- `parse_coverage(profile, module)` — repo‑relative files with executed statements
  (count > 0), tests excluded.
- `parse_compiler_files(output)` — files named by `./foo.go:12:3:` compiler errors.
- `parse_trace_files(output, known)` — repo frames from a panic/goroutine stack, deepest
  first, stdlib frames dropped.
- `rerank(loc_candidates, ev)` — the ladder: compiler‑named → trace‑named → covered (ordered
  by the existing lexical/PageRank position) → covered‑but‑unranked → full prose fallback.
  The old ranking is **never discarded**; it does the ordering and the fallback.

`tools/go_tools.go_test_cover` runs the repro under `-coverpkg=./... -coverprofile=cover.out`
so cross‑package execution is captured even when the test fails.

### 5.3 The repair rework (`agent.py`, `phases/repair.py`, `phases/context.py`)

Coverage narrowing alone did **not** resolve `#1476` — the first attempt (a naïve
multi‑candidate "walk" that tried each covered file **once**) failed, and the failure was
instructive (§11). The rework has three parts, each justified by an observed failure:

1. **The model orders the shortlist, not lexical** (`repair.rank_suspects`). Coverage gives
   ~8 files; the model is asked directly *"the failing test executed these — which contain the
   bug?"* and orders them. On `#1476` the model already identified `regexes.go` in its own
   context search, so asked head‑on it ranks it #2. Graceful fallback: a weak/empty reply
   degrades to the lexical order we started with — never worse than gate‑4.
2. **The target file is loaded before we ask for an edit** (`context.file_excerpt`). The
   "SEARCH text not found" cascade happened because we asked the model to edit files it had
   never read. `file_excerpt` puts the file's exact current text in front of the model so
   SEARCH blocks copy verbatim. Small files go in whole; large files (e.g. `baked_in.go`) are
   reduced to the relevant functions plus short windows around term‑hits — and that hit‑window
   path is what catches a package‑level var like a regex constant.
3. **A bounded budget per file, with feedback** (the loop in `agent.py`). Top‑3 files, **2
   attempts each**, stop at the first verified fix. Feedback carries *within* a file (so a
   fixable build error gets a correction shot) and resets *between* files (a different file's
   error doesn't apply). With no execution evidence this degenerates to the gate‑4 behavior
   (the one primary file × 3 attempts).

**Why 3 files × 2 attempts.** The covered set is ~7–8 files and the model ranks it directly;
if the right file isn't in the model's top 3, a 4th slot rarely rescues it and widens the
false‑positive surface. Two attempts captures the demonstrated recovery value (a first edit
that breaks the build, corrected on the second) without spending budget on a third shot that,
empirically, never lands.

### 5.4 Artifact cache (`caching.py`, `llm/client.py`)

Content‑addressed: the key is `sha256(model + max_tokens + messages)`. Any change to the
prompt, model, or parameters is a natural **miss** — there is nothing to invalidate and
nothing goes stale. Because the entire pipeline downstream of the model is deterministic
*given the model's outputs*, a re‑run with a populated cache reproduces the whole run with
**zero API calls**.

Two deliberate properties:

- **Best‑effort.** Any filesystem error is swallowed — a misbehaving disk can never break a
  run; it just means a miss.
- **Auto‑off for fakes.** Caching enables only for *real* model calls (and respects
  `GO_AGENT_LLM_CACHE=0`). When a fake `completion_fn` is injected (every unit test), caching
  is off, so test runs cannot leak cached values into one another through the shared on‑disk
  cache. Verified by running the suite twice with no `.cache` written.

This is a stronger token saving than prompt caching for *our* workflow: prompt caching
discounts a repeated prefix on the **first** run; the artifact cache makes an entire **re‑run**
free, and we re‑run the gate constantly. (Prompt caching remains a Stage‑6 polish for
first‑run cost; see §12.)

### 5.5 Decision tracer (`tracing.py`, `agent.py`)

A structured, ordered record of two event kinds:

- **tool_call** — an external action: every model call (recorded by the client, **tagged with
  cache hit/miss**), plus `checkout`, `apply_edits`, `validate`, `coverage`.
- **decision** — an internal choice: localize terms+candidates, the evidence + model‑ranked
  targets, each repair attempt's outcome, the final status.

It is orchestration‑level by design: `validate` is one event with its stage outcome
(build/vet/repro), not three leaf calls. Written per‑instance to `.cache/traces/<name>.json`
and returned on `AgentResult.trace`. A real run's trace reads like a flight recorder:

```
 0 localize       [decision]  terms=[...] candidates=[...]
 1 llm            [tool_call] cache=miss input_chars=720
 ...
 5 coverage       [tool_call] covered=7 traced=0 compiled_err=0
 6 evidence       [decision]  has_evidence=True targets=['baked_in.go','regexes.go',...] per_file=2
 7 checkout       [tool_call] ref=...
 9 apply_edits    [tool_call] target=regexes.go applied=1
10 validate       [tool_call] target=regexes.go stage=all ok=True
11 repair_attempt [decision]  n=3 target=regexes.go outcome=verified
13 finalize       [decision]  status=resolved_internally patch_bytes=2310
```

Because every `llm` event carries its cache state, the trace **doubles as the cache proof**:
on a re‑run the same events all read `cache=hit`.

---

## 6. File‑by‑file reference

### New files

| File | What | Why | How |
|---|---|---|---|
| `phases/evidence.py` | the evidence ladder | localize from execution, not prose | pure parsers (coverage/compiler/trace) + `rerank`; no Docker, fully tested |
| `caching.py` | content‑addressed completion cache | re‑run with zero model calls; token saving | `sha256(model+max_tokens+messages)` → `.cache/llm/<key>.json`, atomic, best‑effort |
| `tracing.py` | the decision tracer | "show your work" for an agentic system | ordered tool_call/decision events; `to_dict`/`write` |
| `tests/test_evidence.py` | ladder unit tests | prove parsing + rerank order | synthetic profiles/traces |
| `tests/test_repair_rank.py` | rank + excerpt unit tests | prove model ordering + verbatim loading | fake LLM + temp files |
| `tests/test_robustness.py` | gate‑5 suite | the four gate checks, provable | bounded loop, selection, cache, trace |
| `prompts/rank.md` | the rank prompt | ask the model to order the covered set | filenames‑only reply, graceful parse |

### Changed files

| File | Change |
|---|---|
| `agent.py` | Phase 4c evidence step; model‑rank → top‑3 × 2 repair loop with per‑file feedback and file‑loading; tracer wired through; `trace` on `AgentResult`; **gate‑4 behavior preserved exactly when there is no evidence** |
| `llm/client.py` | transient retry/backoff; cache + tracer hooks on `complete`; `attach_tracer` |
| `tools/go_tools.py` | `go_test_cover` runner + `test_cover_cmd`; tightened timeouts |
| `phases/repair.py` | `rank_suspects` (model orders the coverage‑narrowed shortlist) |
| `phases/context.py` | `file_excerpt` (load a target file's exact text) |
| `eval/run_eval.py` | write the per‑instance `<name>.trace.json` artifact |

---

## 7. Verification philosophy — do‑no‑harm, and the weak‑repro ceiling

The Stage‑4 rule holds unchanged: **submit only a fix the agent verified** (build + vet + the
agent's own reproduction test). Otherwise abstain. We never ship a fix we could not confirm.
Across every Stage‑5 run, no instance crashed the gate, and wrong‑file attempts failed at
**apply** (cheaply, before Docker) rather than submitting garbage.

Stage 5 also names the ceiling of that rule precisely. The agent's verification signal is its
**own** reproduction test. If that test under‑specifies what the hidden gold test checks, a
fix can pass *ours* and fail *theirs*. This is the **weak‑repro ceiling**, and it is the
single structural cause behind both borderline instances:

- `#1284`: the agent writes a building, vet‑clean, repro‑passing edit to the **right file**
  (recall 1.00) — but the content is incomplete vs gold (diff~ 0.27). After Stage 5's
  file‑loading made the model able to *produce* such an edit, `#1284` shifted from a silent
  abstain (gate‑4) to a confident **wrong submission**. We did not violate do‑no‑harm — we
  confirmed the fix; the confirmation mechanism has a ceiling. But the honest reading is that
  we traded an abstain for a wrong patch on this one instance. It does not change the score
  (abstain and wrong both count as not‑resolved), but it is recorded here in full.
- `#1444`: a *different* fix that gold has sometimes accepted (resolved at diff~ 0.20) and
  sometimes rejected (unresolved at diff~ 0.21). It sits on the knife‑edge; which side it
  falls is decided by small model variation, because its repro doesn't pin the fix tightly
  enough.

Solid instances (`#1314`, `#1476`) do **not** swing, precisely because their repros nail the
behavior. The fix for the ceiling is stronger repro generation — deferred as a fenced
experiment (§12) with anti‑overfit guardrails.

---

## 8. Results — measured, across runs

> All runs below are `claude-sonnet-4-5-20250929` at `temperature=0` (our reference model),
> through the **identical** Stage‑5 pipeline, with the real Docker sandbox. `temperature=0`
> is near‑deterministic on a hosted API, **not** bitwise‑identical — and that small residue
> is the whole story of this section.

### 8.1 The progression

| Run | `#1314` | `#1444` | `#1476` | `#1284` | `#1423` | Resolved |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| gate‑4 (Stage 4 baseline) | ✅ 0.99 | ✅ 0.20 | ✗ wrong file | ✗ 0.22 | ⏸ abstain | **2 / 5** |
| + coverage localization (1st walk) | ✅ 0.99 | ✅ 0.20 | ⏸ walk failed | ⏸ noop | ⏸ abstain | 2 / 5 |
| + repair rework | ✅ 0.99 | ✅ 0.20 | ✅ **0.95** | ✗ 0.27 | ⏸ abstain | **3 / 5** |
| + cache & tracer (fresh) | ✅ 0.99 | ✗ 0.21 | ✅ 0.95 | ✗ 0.27 | ⏸ abstain | 2 / 5 |
| + cache & tracer (cached replay) | ✅ 0.99 | ✗ 0.21 | ✅ 0.95 | ✗ 0.27 | ⏸ abstain | 2 / 5 |

The headline event is `#1476`: **wrong‑file in every prior version → resolved at diff~ 0.95**
once coverage surfaced `regexes.go`, the model ranked it, and the file was loaded so the edit
applied. That is the localization wall coming down, and it is *robust* — `#1476` resolves at
0.95 in every run after the rework.

### 8.2 Per‑instance detail (representative run)

| Instance | Status | Recall | diff~ | What happened |
|---|---|:--:|--:|---|
| `validator-1314` | **resolved** | 1.00 | ~0.99 | valid repro try 1; fix matches the gold one‑liner; **rock‑solid across all runs** |
| `validator-1476` | **resolved** | 1.00 | 0.95 | coverage put `regexes.go` in the set; model ranked it #2; first‑two attempts on `baked_in.go` apply‑failed, third on `regexes.go` verified; **solid after rework** |
| `validator-1444` | **borderline** | 1.00 | 0.20–0.21 | right file, valid repro, builds/vets/passes own repro; a *different* fix gold accepts at 0.20 and rejects at 0.21 — **flips run‑to‑run** |
| `validator-1284` | **unresolved** | 1.00 | 0.27 | right file, building, repro‑passing — but incomplete vs gold (needs a large new‑API change); the weak‑repro ceiling |
| `validator-1423` | **noop** | n/a | 0.00 | 3 repro attempts, none fail on base → `no_repro` → abstains; **correct, safe behavior on a panic bug** |

### 8.3 The non‑determinism finding — proven by the cache

The score moved 3/5 → 2/5 between two **fresh** runs, with no code change. Is that our code,
or the model? The cache settled it, and the proof is elegant:

- The cached re‑run shows **`cache=hit` on all 7 model calls** for `#1476` — the run replayed
  run‑1's exact stored outputs.
- That replay produced an **identical** result (2/5, `#1444` unresolved at 0.21).

A cached re‑run is the *only* fully deterministic case — same inputs, same stored outputs. It
reproduced the fresh run exactly. Therefore **our pipeline is deterministic given fixed model
outputs**, and the run‑to‑run swing lives **entirely in the live model calls**. Concretely,
`#1444`'s reproduction test varied between runs (433 chars vs 321 chars — *different tests*),
the model produced a *different* fix, and at diff~ 0.20–0.21 that flipped the verdict.

The cache, built for efficiency, became the instrument that isolated model variance from code
variance. That is the finding: **a deterministic weakness (a thin repro) exposed by a
non‑deterministic model.**

### 8.4 What the data supports — and what it does not

- **Supported:** localization is solved (`#1476` resolves robustly at 0.95; `#1314` at ~0.99).
  The architecture and the evidence ladder work. do‑no‑harm holds (no run crashed; failures
  are abstains or confirmed‑but‑wrong, never broken patches).
- **Supported:** resolution is a **range, 2–3 of 5**, not a point. `#1314` and `#1476` resolve
  reliably; `#1444` and `#1284` are borderline (weak‑repro); `#1423` abstains.
- **Not supported:** any single headline number like "3/5." Reporting 3/5 would be
  cherry‑picking the high end of a known‑variable measurement. The honest claim is the range,
  *with* the mechanism that produces it.

---

## 9. gate‑5 — definition and evidence

gate‑5 is the original four robustness checks plus no‑regression. All green:

| gate‑5 check | Evidence |
|---|---|
| Repair engages and **provably stops at N** (no infinite loop) | `test_robustness.py::test_repair_loop_is_bounded_no_infinite_loop` — always‑failing validate → `attempts ≤ 3`, abstains, empty patch |
| **Selection** picks a validated candidate when one exists | `test_robustness.py::test_selection_submits_a_validated_candidate` — first‑verified is submitted (majority‑vote declined, §2) |
| **Re‑run hits the cache** (zero LLM calls for unchanged inputs) | `test_robustness.py::test_cache_repeat_is_zero_extra_calls_and_persists` *and* the live proof: `#1476` trace went `miss×7` (run 1) → **`hit×7`** (run 2) |
| The **trace lists tool calls** | `test_robustness.py::test_trace_lists_tool_calls_and_decisions` — `tool_call_count > 0`, includes `llm` + `validate`; plus the per‑instance `.trace.json` artifacts |
| **No regression** | all gate‑4 tests pass unchanged; end‑to‑end `gate4.sh` clean on 5/5, `#1314` + `#1476` resolve |

Plus Workstream B reliability invariants in `test_reliability.py` (timeout handled, transient
API recovers, one instance crashing doesn't abort).

**gate‑5 PASSES.** Note what it certifies: the *robustness machinery* is complete and proven.
It deliberately does **not** certify a resolution number — that is reported as a range (§8),
because the honest characterization of a non‑deterministic system is a distribution, not a
point.

---

## 10. Tests

`python -m pytest tests/ -q` → **77 passed** (run twice back‑to‑back with no `.cache`
written, proving the fake‑model cache auto‑disable). Breakdown of the Stage‑5 additions:

- `test_evidence.py` (6) — coverage/compiler/trace parsing; rerank ladder order; compiler &
  trace outrank coverage; no‑evidence → prose order unchanged.
- `test_repair_rank.py` (6) — model ordering; graceful fallback on a junk reply; single
  candidate skips the model; `file_excerpt` whole‑small / windowed‑large / missing‑file.
- `test_robustness.py` (5) — the four gate checks + the cache‑safety guard.
- All prior suites (60) green and unchanged — the proof that the no‑evidence path is
  byte‑for‑byte gate‑4.

Everything runs without Docker or a real model: coverage/compiler/trace parsing is pure; the
agent spine uses a scripted fake model and injected validation; the cache test uses a counting
fake and a temp directory.

---

## 11. Findings — the Stage 5 narrative

**Finding 12 — the embeddings revert set the discipline.** Stage 4 ended by *trying* semantic
retrieval to fix `#1476` localization, measuring it at exactly zero recall gain, and reverting
it. That established the rule that governs Stage 5: diagnostic‑first, measure before claiming,
don't overfit.

**Finding 13 — the coverage diagnostic flipped a wrong prediction.** We predicted `regexes.go`
was a data‑only file with no coverage. The diagnostic showed it has functions and **is**
covered. We were wrong; the cross‑validation caught it before any code shipped. The thesis
("the buggy file executes even when the issue doesn't name it") held on real data.

**Finding 14 — coverage narrows, but does not order; the naïve walk proved it.** The first
implementation narrowed to ~8 files and walked them, trying each **once**, in lexical order.
It failed on `#1476`: attempt 1 *edited* `baked_in.go` and only broke the build (a recoverable
error), but the walk **abandoned it after one shot** and spent the remaining attempts pointing
the model at files it had never read — producing a cascade of "SEARCH text not found." Two
lessons, both acted on: (a) lexical is the wrong ranker for the shortlist — the buggy file is
the *quiet* one; (b) one attempt per file throws away fixable errors. This drove the repair
rework (§5.3): the model orders the shortlist, the file is loaded, and each file gets a bounded
budget with feedback.

**Finding 15 — the `#1476` breakthrough, in the trace.** Post‑rework: coverage narrowed 40→7;
the model ranked `regexes.go` #2; the budget spent its two shots on `baked_in.go` (both
apply‑failed) and **moved on**; `file_excerpt` handed it `regexes.go` whole; it copied the
regex verbatim and fixed it; verified at diff~ 0.95. Every component did its job, in sequence.
The wall that beat every prior version came down — and not by anything `#1476`‑specific.

**Finding 16 — the `#1444` flip, diagnosed by the cache.** Adding the (behavior‑neutral) cache
and tracer coincided with a 3/5 → 2/5 drop, which *looked* like a regression. The cached
re‑run (`hit×7`, identical result) proved the pipeline is deterministic given fixed outputs,
so the swing is model non‑determinism, and `#1444` is a borderline instance at diff~ 0.20–0.21
whose verdict small model variation flips. This is the weak‑repro ceiling (§7), not a bug — and
it is *why* we report a range, not a number.

Across all of Stage 5, **zero wrong‑or‑broken patches were submitted that the agent had not
verified against its own repro.** The one honest blemish is `#1284` shifting from abstain to a
confirmed‑but‑incomplete submission (§7) — recorded, not hidden.

---

## 12. Limitations & future work

- **The weak‑repro ceiling (the one real lever left on the score).** `#1444` and `#1284` flip
  or fail because the agent's own reproduction test under‑specifies what gold checks. Stage 5
  proved this is now the *only* bottleneck on resolution — localization and repair are no
  longer the blockers. **Fenced Stage‑6 experiment:** after a repro passes, push the model to
  harden it (more assertions, the boundary cases the issue names) so a fix must be *actually*
  right to pass. **Guardrails, pre‑committed:** it must not peek at the gold test (that would
  be cheating); and if it does not move the 2–3/5 range, or it overfits to these two
  instances, **revert it** — the same rule that saved us on embeddings.
- **Prompt caching (Stage‑6 token polish).** Anthropic prompt caching discounts a repeated
  prefix on the first run; our repair loop resends a large file excerpt across attempts with
  only the feedback changing — an ideal cacheable prefix. Complementary to the artifact cache
  (which makes *re‑runs* free); this would cut *first‑run* cost.
- **Parallel candidate validation (deferred).** Speed only; revisit at scale (§2).
- **Majority‑vote selection (declined).** Does not address our failure modes; would worsen
  `#1284` (§2).
- **`#1423` panic reproduction.** The agent cannot write a test that reproduces the
  panic‑on‑private‑fields within three tries, so it abstains — correct and safe, but unresolved.
  Likely needs the repro‑hardening work above (feeding the model the real panic text to target).
- **Data‑integrity: `eval/tasks/validator-1314/test.patch` is missing** on the working copy.
  `#1314` still resolves (the agent writes its own repro; the gold `test.patch` is only used
  for scoring), but the file should be restored.

---

## 13. How to run / reproduce

```bash
# from the repo root
python -m pytest tests/ -q          # expect: 77 passed

export LITELLM_LOG=ERROR

# Reference run (Sonnet, temperature=0). First run populates the cache.
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929 bash scripts/gate4.sh

# Re-run: now served from cache. Confirm zero model calls -> every llm event is a hit:
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929 bash scripts/gate4.sh
python -c "import json;print('\n'.join(f\"{e['name']}: {e.get('cache','-')}\" \
  for e in json.load(open('eval/results/agent/validator-1476.trace.json'))['events'] \
  if e['name']=='llm'))"
# expect: hit (x7)

# Characterize the range: run the reference a few times and read the resolved line.
# Expect 2-3/5, with #1314 and #1476 reliably resolved.

# Corroborate on a second model with the cache DISABLED (so it is an honest fresh sample,
# not a replay of stored outputs):
GO_AGENT_LLM_CACHE=0 LLM_MODEL=anthropic/claude-opus-4-7 bash scripts/gate4.sh
```

`.env` selects the model (`LLM_MODEL`, `ANTHROPIC_API_KEY`); `GO_AGENT_LLM_CACHE=0` disables
the result cache for honest corroboration; per‑instance traces land in
`eval/results/agent/<name>.trace.json`.
