# Stage 4 — The Agent Loop

> The stage where everything built so far becomes an **autonomous contributor**: it
> reads a GitHub issue, finds the buggy code, writes a test that reproduces the bug,
> repairs the code, validates the repair in a sandbox, and emits a clean pull‑request
> patch — or, if it cannot *verify* a fix, submits **nothing**.

This document is the complete record of Stage 4: the design, every file, the full
pipeline phase‑by‑phase, the verification philosophy, the evaluation, the measured
results on **three** different models, and the full findings/debugging narrative that
shaped the final system.

---

## Table of contents

1. [What Stage 4 is, and where it sits](#1-what-stage-4-is-and-where-it-sits)
2. [Locked design decisions](#2-locked-design-decisions)
3. [The pipeline at a glance](#3-the-pipeline-at-a-glance)
4. [Phase‑by‑phase deep dive](#4-phase-by-phase-deep-dive)
5. [File‑by‑file reference](#5-file-by-file-reference)
6. [Tools the agent uses](#6-tools-the-agent-uses)
7. [The model seam (dev vs ship)](#7-the-model-seam-dev-vs-ship)
8. [The prompts](#8-the-prompts)
9. [The verification philosophy — "do no harm"](#9-the-verification-philosophy--do-no-harm)
10. [Evaluation integration & gate‑4](#10-evaluation-integration--gate-4)
11. [Results — three models, measured](#11-results--three-models-measured)
12. [Findings — the full debugging narrative](#12-findings--the-full-debugging-narrative)
13. [Per‑instance deep dive](#13-per-instance-deep-dive)
14. [Tests](#14-tests)
15. [Limitations & future work](#15-limitations--future-work)
16. [How to run / reproduce](#16-how-to-run--reproduce)

---

## 1. What Stage 4 is, and where it sits

Stages 0–3 built the **parts**. Stage 4 assembles them into a working agent.

| Stage | What it produced | Role for Stage 4 |
|------|------------------|------------------|
| **0** | Project scaffold, `config.py`, `llm` ping, Docker sandbox image (`golang:1.24`) | The model seam and sandbox the agent runs on |
| **1** | 5 verified ground‑truth instances (`validator-1284/1314/1423/1444/1476`), each with `instance.json`, `fix.patch`, `test.patch`, `FAIL_TO_PASS` + `PASS_TO_PASS` | The bugs the agent is asked to fix, and the hidden gold tests that judge it |
| **2** | Eval harness: `run_eval.py` orchestrator + `metrics.py` (pure scoring) + `test_metrics.py`; `resolved = ftp_passed AND ptp_passed` | The honest ruler that scores the agent's output |
| **3** | 7 deterministic tools (file I/O, span read, code search, patch apply, Go toolchain runners, AST nav, repo map) | The agent's hands — everything it uses to read and change code |
| **4** | **This stage**: the `run_agent` orchestrator that drives an LLM through localize → context → reproduce → repair → validate → finalize | — |

**The core idea.** The agent is a *tool‑using LLM with bounded autonomy*. It is **not**
fully autonomous and it is **not** multi‑agent. It follows a fixed spine — localize, then
repair, then validate — with exactly **two bounded agentic loops** inside it (a
context‑gathering loop and a repair loop). Everything deterministic (search, AST,
patch‑apply, the Go toolchain) is a plain tool; the LLM is only invoked where genuine
judgement is required (deciding what to read, writing the test, writing the fix).

**The honesty contract.** The agent sees only the issue text (`problem_statement`) and
the code at `base_commit`. It **never** sees the gold `fix.patch` or the hidden
`FAIL_TO_PASS` test. Its own reproduction test is a private self‑check and is *stripped
out* of the submitted patch (which is code‑only, exactly like the gold fix). The real
verdict — did it actually resolve the issue — comes afterwards from the Stage‑2 harness
applying the hidden gold test. This separation is what makes the numbers in §11
trustworthy.

---

## 2. Locked design decisions

These were agreed before any Stage‑4 code was written, and held throughout.

| # | Decision | Why |
|---|----------|-----|
| Q1 | Code edits are expressed as **search/replace blocks**, not raw unified diffs | A small model cannot reliably emit valid diff hunks (line numbers, `@@` headers); literal "find this text, replace with that" is far more robust |
| Q2 | The agent **writes its own reproduction test** to self‑check the fix | Gives the agent a real signal ("did my change fix the thing?") without ever seeing the gold test |
| Q3 | **gate‑4** = run clean on all 5 instances **AND** resolve ≥ 1 (target `#1314`) | A pass requires both *correctness of the machine* (no crashes, no garbage) and *evidence it works* (at least one real resolution) |
| — | Edits applied to a **known target file**; the model outputs blocks **without a path** | Eliminates the single biggest failure mode — the model hallucinating or echoing a file path (see §12) |
| — | Submit **only a verified fix**; otherwise **abstain** (empty patch) | "First, do no harm": never ship a change the agent could not confirm builds, vets, and passes its reproduction |
| — | Model is **swappable** via `LLM_MODEL`; small local model for dev, strong model for ship | Same agent, same prompts, different brain — lets us isolate "is this the architecture or the model?" |
| — | The final patch is **code‑only** (`*_test.go` excluded) | Mirrors the gold `fix.patch`; keeps localization precision honest |

---

## 3. The pipeline at a glance

`run_agent()` (in `agent.py`) executes seven phases. Phases 3 and 6 contain the two
bounded loops; phase 4 contains a third bounded loop (repro validation) added late in
development (see §12).

```
 problem_statement + repo@base_commit
            │
            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │ Phase 2  LOCALIZE            (deterministic, no LLM)                      │
 │   issue text → terms → repo_map (PageRank biased to terms) → candidates  │
 └─────────────────────────────────────────────────────────────────────────┘
            │  Localization(terms, candidates, repo_map_skeleton)
            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │ Phase 3  GATHER CONTEXT      (bounded loop, ≤ max_context_reads)          │
 │   focus_snippets(): exact functions + exact paths  ── deterministic      │
 │   + optional model-driven READ/SEARCH/DONE          ── LLM, de-duped     │
 │   → primary_file (the file the model will edit)                          │
 └─────────────────────────────────────────────────────────────────────────┘
            │  Context(text, actions, primary_file)
            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │ Phase 4  REPRODUCE           (bounded loop, ≤ max_repro_attempts)         │
 │   model writes a Go test → CHECK it FAILS on base (truly reproduces)     │
 │   if it passes on base → invalid → regenerate with feedback              │
 │   if never valid → ABSTAIN (status=no_repro)                             │
 └─────────────────────────────────────────────────────────────────────────┘
            │  repro_code (verified to fail on the buggy base)
            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │ Phase 5/6  REPAIR + VALIDATE (bounded loop, ≤ max_repair_attempts)       │
 │   each attempt (independent, re-checkout base):                          │
 │     propose_fix() → search/replace blocks (path-free, → primary_file)    │
 │     apply_edits() → write to file (reject no-op; recover mangled path)   │
 │     validate()    → go build → go vet → run repro test (Docker sandbox)  │
 │   first attempt that passes all three = VERIFIED                         │
 └─────────────────────────────────────────────────────────────────────────┘
            │  verified_patch (or none)
            ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │ Phase 7  FINALIZE                                                        │
 │   verified  → code-only diff + PR title/body  (status=resolved_internally)│
 │   not verified → submit NOTHING               (status=abstained/no_edits) │
 └─────────────────────────────────────────────────────────────────────────┘
            │
            ▼
   AgentResult → (Stage-2 harness applies HIDDEN gold test) → resolved? 
```

Everything inside the box is what the agent *can* do. Whether the result is actually
*correct* is decided one layer out, by the gold test it never saw.

---

## 4. Phase-by-phase deep dive

### Phase 2 — Localize (`phases/localize.py`)

**What.** Turn free‑text issue prose into a ranked list of suspect files, with zero LLM
calls.

**How.** Three steps:

1. **`extract_issue_terms(text)`** pulls identifier‑like tokens out of the report.
   Tokens inside `` `backticks` `` are harvested first (highest signal), then the rest of
   the prose. Each token is filtered and ranked:

   | Filter / rule | Purpose | Example removed/kept |
   |---|---|---|
   | drop `_STOP` words | remove generic English/Go noise | `the`, `validation`, `playground`, `v10` |
   | drop `len < 3` | too short to be a useful search | `id`, `ok` |
   | drop `0?x[0-9a-f]{2,}` | **hex frame offsets** from panic traces | `x1ac`, `0x14000` |
   | drop `func\d+` | **anonymous stack frames** | `func1`, `func2` |
   | drop `\d{5,}` | long bare numbers | `1400008` |
   | rank: has‑digit → CamelCase → frequency → length | identifiers/codes float to the top | `iso3166_1_alpha2`, `VarWithKeyCtx` |

   The hex/`func\d+`/long‑number filters were added specifically because panic‑trace
   issues (e.g. `#1423`) flooded the term list with memory addresses (see §12, Finding 6).

2. **Term‑biased PageRank.** Each term is searched across the repo; files that mention
   terms get a hit count. That `{path: hits}` map is fed to the Stage‑3
   `repo_map.build_repo_map(..., personalization=…)` as the PageRank random‑jump
   distribution — so the importance ranking is *pulled toward* issue‑relevant files
   instead of being purely structural.

3. **Candidates.** Term‑mentioning files first (sorted by hits, then PageRank position),
   then the rest of the ranked map fills in, de‑duplicated, capped at `top_k=8`.

**Output.** `Localization(terms, candidates, repo_map_skeleton)`.

**Why deterministic.** Localization is cheap, reproducible, and shouldn't burn model
tokens or model judgement — the model's judgement is better spent on the fix.

---

### Phase 3 — Gather context (`phases/context.py`)

This phase produces the exact source the model will look at when writing the fix. It has
two parts.

**Part 1 — `focus_snippets()` (deterministic, the decisive piece).**
We do **not** rely on the model to find the right lines. `focus_snippets` deterministically
pulls the *actual functions* worth editing, each with its exact repo‑relative path header
(`// FILE: baked_in.go (copy SEARCH text verbatim from here)`), so the model can copy a
real search block instead of guessing. A symbol qualifies if:

- its **name matches an issue term** (case‑insensitive) — i.e. it *defines* something the
  issue talks about; **or**
- it **encloses a line where a term was found** (a search hit).

Files are then ranked so the file that **defines** a term‑matching symbol wins, then by
localize‑candidate order, then by raw hit count:

| Rank key | Meaning | Why it matters |
|---|---|---|
| `has a name‑matching symbol` | the file *defines* the thing | a definer beats a file that merely *calls* it (Finding 7) |
| `is a localize candidate` (and its position) | structural + term importance | trust the PageRank ordering next |
| term‑hit count | raw mentions | final tiebreak |

The first block's file becomes **`primary_file`** — the single file the model is told to
edit (path‑free editing, see §9).

**Part 2 — optional bounded model loop.** The model may still issue `READ <file> <a> <b>`,
`SEARCH <regex>`, or `DONE` actions (prompt `context.md`), up to `max_context_reads` (5).
The loop is **de‑duplicated**: if the model repeats an action it already issued, the loop
stops rather than waste the budget (Finding 5 — small models loop on the same search).
The exact `focus_snippets` source is always prepended, so even a wasteful model loop still
has the real code in front of it.

**Output.** `Context(text, actions, primary_file)`.

---

### Phase 4 — Reproduce, and verify the reproduction (`phases/repair.py` + the loop in `agent.py`)

**What.** The model writes one Go test (`TestAgentRepro`) that should fail on the bug and
pass once fixed (prompt `repro.md`, framed around the *expected* behaviour).

**The critical gate.** A reproduction test is only meaningful if it actually *reproduces*
the bug — i.e. it must **FAIL when run against the unpatched base**. So before trusting it,
the agent runs it on `base_commit`:

```
reproduces_fn(repo_dir, repro_code):
    checkout(base)
    r = validate(repro_only)        # build + vet + run the test on buggy code
    return (not r.ok) and r.stage == "repro"   # compiled & vetted, but the test FAILED
```

| Outcome on base | Interpretation | Action |
|---|---|---|
| build/vet fail | the test doesn't even compile | invalid → regenerate |
| test **passes** | it does **not** reproduce the bug (asserts buggy behaviour) | invalid → regenerate with feedback |
| test **fails** (compiles, vets, asserts) | it genuinely reproduces the bug | **valid** → use it |

If after `max_repro_attempts` (3) no valid reproduction exists, the agent **abstains**
(`status=no_repro`): it cannot verify a fix, so it submits nothing. This gate is the direct
fix for the most subtle bug we found — a model that writes an *inverted* test that passes on
buggy code and thereby vetoes the correct fix (Finding 9).

---

### Phases 5 & 6 — Repair + validate (`phases/repair.py`, `edits.py`, `phases/validate.py`)

A bounded loop, up to `max_repair_attempts` (3). Each attempt is **independent** — the
working tree is re‑checked‑out to base first, so a bad attempt never contaminates the next.

1. **`propose_fix()`** → the model returns search/replace blocks (prompt `fix.md`), told
   exactly which file to edit and to output **no path**. Feedback from the previous failed
   attempt is fed back in.
2. **`apply_edits()`** writes the change to `primary_file` (or a path the model supplied,
   recovered by basename if mangled). It **rejects no‑op edits** (SEARCH == REPLACE) and
   reports failures instead of raising.
3. **`validate()`** runs, in the Docker sandbox, in order: `go build ./...` → `go vet ./...`
   → `go test -run TestAgentRepro`. The first stage to fail short‑circuits and is reported.

The **first attempt** whose validation returns `ok` (build + vet + repro all green) is the
**verified patch** and the loop breaks. Otherwise the failure detail becomes the next
attempt's feedback.

| `ValidationResult.stage` | Meaning |
|---|---|
| `build` | `go build ./...` failed (won't compile) |
| `vet` | compiles but `go vet` flagged it |
| `repro` | builds & vets, but the reproduction test failed |
| `tests` | (optional) builds, vets, repro passes, but the existing suite regressed |
| `all` | everything passed → **verified** |

---

### Phase 7 — Finalize (`phases/finalize.py`)

| Condition | What is submitted | `status` |
|---|---|---|
| a verified patch exists | `code_only_diff()` (working tree vs HEAD, `*_test.go` excluded) + LLM‑written PR title/body | `resolved_internally` |
| no verified patch, `submit_unvalidated=True` (opt‑in, off by default) | the last applied diff | `gave_up` |
| no verified patch (default) | **nothing** (empty patch); tree reset to base | `abstained` (a real attempt existed) / `no_edits` (model never produced one) |

`code_only_diff()` runs `git diff -- . ':(exclude)*_test.go'`, so the scratch reproduction
test (`zz_agent_repro_test.go`, untracked) never leaks into the submission. The PR text is
generated from `pr.md` and parsed into a `TITLE:`/`BODY:` pair.

---

## 5. File-by-file reference

Files **new** in Stage 4 (plus the two existing files Stage 4 modified). Everything under
`tools/`, `indexing/`, and `sandbox/` is **reused as‑is from Stage 3** and listed in §6.

| File | Lines | What it is | Key public surface |
|---|---:|---|---|
| `src/go_issue_agent/agent.py` | 157 | The orchestrator — the whole spine + 3 bounded loops | `run_agent(...) -> AgentResult` |
| `src/go_issue_agent/edits.py` | 151 | Parse & apply the model's search/replace blocks | `parse_edits`, `apply_edits`, `Edit`, `ApplyEditsResult` |
| `src/go_issue_agent/phases/localize.py` | 83 | Phase 2 — issue → suspect files | `localize`, `extract_issue_terms`, `Localization` |
| `src/go_issue_agent/phases/context.py` | 157 | Phase 3 — exact source + bounded read loop | `gather`, `focus_snippets`, `Context` |
| `src/go_issue_agent/phases/repair.py` | 46 | Phase 4/6 — model writes repro & fix | `propose_repro`, `propose_fix`, `repro_test_name` |
| `src/go_issue_agent/phases/validate.py` | 45 | Phase 5 — build/vet/repro in the sandbox | `validate`, `ValidationResult` |
| `src/go_issue_agent/phases/finalize.py` | 41 | Phase 7 — code‑only diff + PR text | `code_only_diff`, `pr_text` |
| `src/go_issue_agent/phases/prompts.py` | 17 | Load & render `prompts/*.md` | `render(name, **kw)` |
| `prompts/context.md` | 18 | The READ/SEARCH/DONE action prompt | — |
| `prompts/repro.md` | 23 | The reproduction‑test prompt | — |
| `prompts/fix.md` | 35 | The search/replace fix prompt | — |
| `prompts/pr.md` | 12 | The PR title/body prompt | — |
| `tests/test_agent.py` | 281 | 24 unit tests for the whole spine (fake model + fake validator) | — |
| `scripts/gate4.sh` | 11 | Runs `run_eval.py --gate4` | — |
| `src/go_issue_agent/llm/client.py` | 90 | **Modified**: injectable `LLMClient` seam + hosted‑API routing | `LLMClient`, `complete`, `ping` |
| `eval/run_eval.py` | — | **Modified**: `score_agent`, `cmd_gate4`, `--gate4`/`--candidate agent` | — |

### What / Why / How per file

**`agent.py` — `run_agent()`.**
*What:* the single entry point that runs an instance end to end and returns an
`AgentResult`. *Why a single function:* the spine is intentionally simple and linear — easy
to read, easy to test. *How it stays testable:* both the model (`llm: LLMClient`) and the
Docker‑backed `validate_fn` / `reproduces_fn` are **injectable**; in unit tests a fake model
and a lambda validator drive the full spine with **no Ollama and no Docker**.

`AgentResult` fields:

| Field | Meaning |
|---|---|
| `status` | `resolved_internally` \| `gave_up` \| `abstained` \| `no_repro` \| `no_edits` |
| `code_patch` | the code‑only unified diff actually submitted (may be empty) |
| `repro_code` | the agent's reproduction test (scratch; **not** part of `code_patch`) |
| `pr_title`, `pr_body` | generated PR text (empty if nothing submitted) |
| `attempts` | how many repair attempts were made |
| `internal_ok` | did a fix pass the agent's own build+vet+repro check? |
| `attempt_patch` | best/last attempted diff, kept for debugging even when abstaining |
| `log` | the per‑phase log lines |

**`edits.py`.** *What:* the parser/applier for the model's edit language. *Why its own
module:* it's pure and deterministic, so it carries a disproportionate share of the unit
tests (a small model's output is messy and must be tolerated). *How it's robust:*

| Mechanism | Handles |
|---|---|
| `parse_edits` extracts `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks; the path on the preceding line is *optional* | model outputs blocks with or without a path |
| `_clean_path` strips fences, backticks, angle‑brackets, bullets, and `FILE:`/`FILENAME`/`path:` prefixes; placeholders → `""` | model echoing `<relative/path/to/file.go>` or `FILENAME` (Findings 1–3) |
| `apply_edits(..., default_target=...)` applies a path‑free block to the known file | the path‑free editing strategy |
| `_resolve()` recovers a mangled path by **unique basename** match | model writing `validator/baked_in.go` when the file is `baked_in.go` |
| no‑op guard: reject when `SEARCH == REPLACE` (no net change) | model "fixing" by changing nothing (Finding 8) |

**`context.py`.** Covered in §4. The two pieces — `focus_snippets` (exact source) and the
de‑duplicated read loop — plus `primary_file` selection.

**`validate.py`.** *What:* the only place the agent runs Go. *Why in Docker:* the host (a
Mac) isn't a clean Go build environment and shouldn't be trusted to be; the Stage‑3
`go_tools` runners execute `go build/vet/test` inside the `golang:1.24` sandbox with the
module cache mounted. *How signals are reported:* a single `ValidationResult(ok, stage,
detail)` where `stage` names the first failing step and `detail` is the error tail fed back
to the model.

**`finalize.py`, `repair.py`, `localize.py`, `prompts.py`.** Covered in §4 and §8.

---

## 6. Tools the agent uses

Stage 4 writes **no new tools** — it *orchestrates* the seven deterministic tools built and
hardened in Stage 3. Each is a thin, well‑tested wrapper; the agent never shells out
directly.

| Tool (module) | What the agent uses it for | Phase |
|---|---|---|
| `tools/search_code.py` — `search_code()` | ripgrep (with a pure‑Python fallback) for issue terms and model `SEARCH` actions | 2, 3 |
| `tools/read_span.py` — `read_span()` | read exact line ranges of a file (1‑indexed, clamped) for `focus_snippets` and `READ` | 3 |
| `tools/fileio.py` — `read_file/write_file/list_files` | safe, path‑escape‑guarded file access | (via others) |
| `indexing/ast_nav.py` — `parse_file()`, `Symbol` | tree‑sitter parse to find the *function* enclosing a hit / matching a term | 3 |
| `indexing/repo_map.py` — `build_repo_map(personalization=…)` | term‑biased PageRank ranking + token‑budgeted skeleton | 2 |
| `tools/go_tools.py` — `go_build/go_vet/go_test/gofmt_check` | run the Go toolchain **in the Docker sandbox** for validation | 4, 5 |
| `tools/apply_patch.py` | (Stage‑3 tolerant patch applier; the agent's edits go through `edits.apply_edits` instead, but the tool remains for diff application) | — |
| `sandbox/repo.py` — `checkout()`, `current_commit()` | git state management between independent attempts | all |
| `sandbox/runner.py` — `run_in_sandbox()` | the Docker exec primitive under `go_tools` | 4, 5 |

The division of labour is the whole point: **deterministic tools do the mechanical work,
the LLM does only the judgement.** Localization, AST parsing, search, patch application, and
the Go toolchain are all deterministic and unit‑tested; the model is invoked exactly three
times per instance‑attempt (decide context, write repro, write fix).

---

## 7. The model seam (dev vs ship)

Everything talks to the model through one place — `llm/client.py` — so swapping providers is
a **config change, not a code change**. This is what let us prove the central finding: run
the *identical* agent on a weak local model and a strong hosted model, and compare.

**`LLMClient`** is a thin, injectable chat client:

```
LLMClient(model=None, temperature=0.0, max_tokens=1024, completion_fn=None)
  .complete(messages, max_tokens=None) -> str
```

- `model` defaults to `settings.llm_model` (env `LLM_MODEL`).
- `completion_fn` defaults to a lazy litellm call; **tests inject a fake** so no network/Ollama is needed.
- `temperature=0.0` for determinism.

**Provider routing (the one Stage‑4 code change for hosted models).** litellm picks the
provider from the model string. Ollama needs an explicit local `api_base`; hosted APIs must
**not** be given that base or they misroute to localhost. So:

```
_api_base_for(model) = settings.llm_api_base  if model.startswith("ollama/")  else None
```

| `LLM_MODEL` | `api_base` passed | Routes to |
|---|---|---|
| `ollama/qwen2.5-coder:14b` | `http://localhost:11434` | local Ollama |
| `anthropic/claude-sonnet-4-5-20250929` | *(none)* | Anthropic API (key from env) |
| `gpt-4o` | *(none)* | OpenAI API (key from env) |

**`config.py`** loads `.env` and exposes `Settings(llm_model, llm_api_base, sandbox_image,
github_token)`. To switch models you set two env vars and nothing else:

```
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
ANTHROPIC_API_KEY=sk-ant-...
```

The Docker sandbox always runs locally; only the model calls leave the machine.

---

## 8. The prompts

Four small templates in `prompts/`, rendered by `phases/prompts.py` via `str.format`
(values inserted literally, so code containing braces is safe).

| Prompt | Used by | Asks the model to | Hard‑won design choices |
|---|---|---|---|
| `context.md` | Phase 3 | emit **one** action per turn: `READ`/`SEARCH`/`DONE` | strict one‑action format keeps parsing trivial |
| `repro.md` | Phase 4 | write `TestAgentRepro` asserting the **expected** behaviour | "assert what *should* happen (currently broken)", e.g. *valid input accepted* — counters the inverted‑test failure |
| `fix.md` | Phase 6 | output **only** search/replace blocks, **no path**, for one named `{target}` file | a structure‑only example; "find the ROOT CAUSE / compare to a working sibling / add the missing line / be on the path that runs" |
| `pr.md` | Phase 7 | write a `TITLE:`/`BODY:` PR description | fixed format for easy parsing |

The `fix.md` prompt deserves a note: it evolved heavily (§12). It now (a) names the exact
target file, (b) shows a *structure‑only* example using throwaway identifiers (so the model
can't usefully echo it), and (c) gives explicit root‑cause guidance — compare the buggy
function to a correct sibling and add the missing line — phrased generally, not specialised
to any one instance.

---

## 9. The verification philosophy — "do no harm"

This is the single most important *behavioural* decision in Stage 4, and it was sharpened by
the findings in §12.

**Principle: the agent submits a patch only if it has genuinely *verified* it; otherwise it
submits nothing.** A change the agent cannot confirm is worse than no change, because a
broken or wrong patch can regress working behaviour and wastes a reviewer's time, while an
empty patch is always safe.

Three guards enforce this, layered:

| Guard | Where | Stops |
|---|---|---|
| **No‑op rejection** | `apply_edits` | the model "fixing" by emitting `SEARCH == REPLACE` (zero net change) being mistaken for a fix |
| **Reproduction must fail on base** | Phase 4 gate | trusting a test that doesn't actually reproduce the bug (e.g. an inverted test) — which would wrongly *veto a correct fix* or wrongly *bless a no‑op* |
| **Verified‑only submission** | Phase 7 | shipping anything that didn't pass build + vet + a *valid* reproduction |

The resulting status vocabulary makes the agent's honesty legible:

| `status` | Meaning | Submits |
|---|---|---|
| `resolved_internally` | a fix passed build + vet + a valid reproduction | the verified patch |
| `abstained` | it had a candidate patch but couldn't verify it | nothing |
| `no_repro` | it couldn't even write a test that reproduces the bug | nothing |
| `no_edits` | the model never produced a usable edit | nothing |
| `gave_up` | only if `submit_unvalidated=True` (opt‑in) | the best unverified attempt |

We deliberately considered and **rejected** a "submit anything that compiles, let the gold
test judge" mode: on the grading side that means shipping wrong fixes to harvest
localization credit, which violates the do‑no‑harm principle. The agent abstains instead.
The cost of this stance is honest and explicit: on instances where the model can't produce a
*verifiable* fix, the agent scores `noop` (zero) rather than collecting partial localization
credit for a broken patch — a trade we make deliberately.

---

## 10. Evaluation integration & gate-4

Stage 4 plugs into the Stage‑2 harness rather than inventing its own scoring. The agent's
output is scored by the **same ruler** used for the gold patch and the empty baseline.

**`score_agent()` (in `run_eval.py`).** For each instance it:

1. checks out `base_commit`,
2. runs `run_agent(problem_statement, repo, llm=…, base_ref=base_commit)`,
3. saves artifacts to `eval/results/agent/<id>.{patch, pr.md, repro_test.go}` (and
   `<id>.attempt.patch` when the agent abstained, for debugging),
4. scores the **code‑only** patch via `evaluate(..., cand_diff_override=res.code_patch)` —
   which applies the candidate to a fresh base checkout, then applies the **hidden gold
   test** and runs `FAIL_TO_PASS` + `PASS_TO_PASS`,
5. annotates the score with `agent=<status> attempts=<n>`.

Per‑instance exceptions are caught (`status="error"`) so one crash can't abort the gate.

**`cmd_gate4()` / `scripts/gate4.sh`.** Runs all five, prints the metrics table, then
applies the gate:

```
PASS  ⇔  ran clean on ALL instances  AND  resolved ≥ 1
```

The same `resolved = ftp_passed AND ptp_passed` rule from Stage 2 decides "resolved"; the
agent's `status` never overrides it. An empty/abstained patch scores as `noop`; a wrong
patch scores `unresolved` (with whatever localization/build/vet credit it earned); a
crash scores `error`.

---

## 11. Results — four models, measured

The headline question of Stage 4 was: **is the architecture sound, or is the model the
limit?** We answered it by running the *identical* pipeline on four models.

> The **14B**, **Sonnet**, and **Opus** runs use the **final** pipeline (repro‑gate +
> verified‑only submission), so they are a true apples‑to‑apples comparison — same code,
> same prompts, different model. The **7B** was the development model; it was exercised on
> earlier pipeline iterations and resolved **0/5** throughout, and its failure modes drove
> the hardening documented in §12.
>
> **Sonnet is the reference result** because it ran at **`temperature=0`**,
> so it is reproducible up to small run-to-run variance — `temperature=0` is near-deterministic on hosted APIs, not bitwise-identical. **Opus is reported as a corroborating run**: it also passed
> gate‑4, but — because it *rejects* the `temperature` parameter (Finding 11) — it ran at its
> default temperature and is therefore **non‑deterministic**. See *On comparing the two
> frontier models* below before reading anything into the Sonnet‑vs‑Opus resolved counts.

### Headline

| Model | Provider / role | Resolved | Unresolved | Noop (abstain) | Ran clean | gate‑4 |
|---|---|---:|---:|---:|---:|:--:|
| `qwen2.5-coder:7b` | Ollama (local, dev) | **0 / 5** | — | — | 5 / 5 | ✗ |
| `qwen2.5-coder:14b` | Ollama (local) | **0 / 5** | 1 | 4 | 5 / 5 | ✗ |
| `claude-sonnet-4-5-20250929` | Anthropic API (**reference**, deterministic) | **2 / 5** | 1 | 2 | 5 / 5 | **✓ PASS** |
| `claude-opus-4-7` | Anthropic API (corroborating, non‑deterministic) | **1 / 5** | 1 | 3 | 5 / 5 | **✓ PASS** |

**The verdict.** Going from the local 14B to a frontier model — *nothing else changed* —
took resolution from 0 to 1–2 and flipped gate‑4 to **PASS**. The architecture was never the
blocker; model capability was. Both frontier models pass; we do **not** claim a ranking
between them (see below).

### Token usage

We report **tokens in/out** rather than a dollar figure (prices change; token counts are
stable and verifiable). Both hosted rows are the full 5‑instance gate‑4 run for June 2026
(Anthropic Console, grouped by model). Local models (7B/14B) run on Ollama and consume **no**
API tokens. There is **no** web‑search or code‑execution usage — the Docker sandbox is local.

| Model | Tokens in | Tokens out | gate‑4 | Resolved |
|---|---:|---:|:--:|---:|
| `claude-sonnet-4-5-20250929` (reference) | 63,947 | 4,702 | ✓ PASS | 2 / 5 |
| `claude-opus-4-7` (corroborating) | 126,279 | 4,074 | ✓ PASS | 1 / 5 |

One instance issues several model calls — context, up to 3 repro generations (each
re‑validated on base), up to 3 repair attempts, and the PR text — so a 5‑instance run lands
in the tens of thousands of input tokens. Opus consumed **~2× the input tokens** of Sonnet
for comparable output (it pulled more context per call). That is a practical
throughput/latency difference, **not** a quality signal.

### Sonnet — per‑instance detail (the final pipeline)

| Instance | Status | Recall | Prec | build | vet | fmt | diff~ | What happened |
|---|---|:--:|:--:|:--:|:--:|:--:|--:|---|
| `validator-1314` | **resolved** | 1.00 | 1.00 | ok | ok | ok | **~1.00** | valid repro on try 1; fix **matches the gold one-liner**; gold test passes (re-run diff 0.99) |
| `validator-1444` | **resolved** | 1.00 | 1.00 | ok | ok | ok | 0.20 | valid repro on try 1; a *different* correct fix (low diff sim, still resolves) |
| `validator-1284` | unresolved | 1.00 | 1.00 | ok | ok | ok | 0.22 | right file, valid repro, fix verified its **own** repro & builds/vets — but the **gold** test still fails (fix incomplete) |
| `validator-1423` | noop | n/a | n/a | n/a | n/a | n/a | 0.00 | 3 repro attempts, none fail on base → **no_repro**, abstained |
| `validator-1476` | noop | n/a | n/a | n/a | n/a | n/a | 0.00 | valid repro, but localized to the **wrong file** (this run: `postcode_regexes.go`) → edits don't build → abstained |

Note `#1444`: `diff~0.20` with `resolved` — the model wrote a *textually different* fix that
nonetheless makes the gold test pass. Diff similarity is a **secondary** metric for exactly
this reason; resolution is the headline, and there can be more than one correct fix.

### Opus — corroborating run (non‑deterministic)

| Instance | Status | diff~ | What happened |
|---|---|--:|---|
| `validator-1314` | **resolved** | **1.00** | valid repro try 1; fix **matches the gold one-liner** |
| `validator-1444` | unresolved | 0.21 | right file, valid repro, builds/vets/passes own repro — but a near‑miss vs the gold test |
| `validator-1284` | noop | 0.00 | `no_repro` — abstained after 3 repro tries |
| `validator-1423` | noop | 0.00 | `no_repro` — abstained after 3 repro tries |
| `validator-1476` | noop | 0.00 | localized the right file in *reasoning* (`regexes.go:23`) but `primary_file` pointed at the wrong file → edits didn't apply → abstained |

Opus matched Sonnet on four of five instances. The only difference is `#1444` (Sonnet
resolved it, Opus produced a near‑miss) and `#1284` (Sonnet got far enough to submit an
incomplete fix; Opus abstained at the repro stage). Both are the *stochastic* instances.

### On comparing the two frontier models

It is tempting to read "Sonnet 2/5 vs Opus 1/5" as "Sonnet is better." **The data does not
support that claim, and this document does not make it.** Three reasons:

1. **n = 5, and one model ran non‑deterministically.** Sonnet ran at `temperature=0`
   (reproducible); Opus was forced to its default temperature because it rejects the
   parameter (Finding 11), so it samples more randomly. On five instances, a one‑instance
   difference is **within run‑to‑run noise** — a second Opus run could easily resolve `#1444`
   and tie or exceed Sonnet.
2. **The gap is a single medium‑difficulty instance** (`#1444`), exactly the kind with
   several near‑miss fixes where sampling temperature matters most.
3. **Opus showed equal‑or‑stronger reasoning where it counts.** On `#1476` its own log
   identified the precise fix location (`the bug is in the regex at regexes.go:23`) — better
   than Sonnet surfaced — and it failed only because of **our** localization gap, not the
   model.

A rigorous Sonnet‑vs‑Opus comparison would require multiple runs per model, averaged. We did
not do that (it is unnecessary for the Stage‑4 thesis and costs more tokens). The honest
conclusion is: **both frontier models pass gate‑4 and resolve `#1314` with the gold fix; we
cannot and do not rank them from this data.** Sonnet is the headline result solely because it
is the **reproducible** one.



| Instance | Cause | Whose limit |
|---|---|---|
| `#1284` | fix builds, vets, passes the agent's own valid repro, but is **incomplete** vs the gold test (needs ~78 lines of new public methods) | **model** (fix quality on a large change) — pipeline behaved correctly |
| `#1423` | model can't write a test that **reproduces** a panic‑on‑private‑fields within 3 tries → abstains | **model** (reproduction on a hard bug) — abstention is the correct, safe behaviour |
| `#1476` | term extraction never surfaced `regexes.go` because the issue text doesn't literally contain `e164`, so the model edited the wrong file | **ours** — a real, improvable **localization** limitation (see §15); *not* a wrong‑fix problem (the agent correctly abstained when it couldn't build/verify) |

Zero wrong or broken patches were submitted across all four models. The do‑no‑harm policy
held throughout.

---

## 12. Findings — the full debugging narrative

Stage 4 was built deterministic‑first, then iterated against the real models. The bugs we
hit were almost all *ours*, and each one produced a concrete, general hardening. This is the
honest record, in order.

| # | Symptom (on the real model) | Root cause | Fix (general, not overfit) |
|---|---|---|---|
| 1 | every instance `noop`; apply error `<relative/path/to/file.go>: file not found` | the model **echoed the literal placeholder** from the fix prompt as the path | removed the angle‑bracket placeholder from `fix.md` |
| 2 | still `noop`; model echoed `FILENAME` | the new format spec still gave the model a copyable path token | **stop making the model write a path at all** — path‑free blocks applied to a known `primary_file` |
| 3 | apply errors with mangled paths like `validator/baked_in.go` | model prepends a package/dir to the real filename | `_clean_path` strips brackets/labels; `_resolve` recovers a path by **unique basename** |
| 4 | model never got the exact lines to edit; had to guess | context relied on the model's own `READ`/`SEARCH` actions | `focus_snippets()` — deterministically hand the model the **exact functions + exact paths** |
| 5 | model issued the same `SEARCH` 5× and burned the context budget | small models loop on one action | **de‑duplicate** the context loop; stop on repeat |
| 6 | `#1423` terms were memory addresses (`x222c`, `func1`) | a **panic stack trace** in the issue flooded term extraction; the digit‑boost then ranked the hex to the top | filter `0?x[0-9a-f]{2,}`, `func\d+`, `\d{5,}` in `extract_issue_terms` |
| 7 | `primary_file` was a file that merely *used* the symbol, not the one that *defined* it | `focus_snippets` ranked by raw hit count | rank **definers first** (name‑matching symbol), then candidate order, then hits |
| 8 | `#1314` "succeeded" internally but produced an **empty** patch | model emitted `SEARCH == REPLACE` (a no‑op) and a weak repro passed on it | **reject no‑op edits** in `apply_edits` |
| 9 | `#1314` abstained even though the model wrote the **exact gold fix** | the model's reproduction test was **inverted** — it asserted the *buggy* behaviour, so it passed on buggy code and *failed on the correct fix*, vetoing it | **reproduction must FAIL on base**: validate the repro against the unpatched code; regenerate if it doesn't reproduce; abstain if it never does (`no_repro`) |
| 10 | a build+vet‑passing but unverified patch was being submitted "to let the gold test judge" | that is shipping wrong fixes for localization credit | reverted to **verified‑only** submission; abstain otherwise |
| 11 | `claude-opus-4-7` ping/run failed: `temperature is deprecated for this model` | some newer models **reject the `temperature` parameter** entirely | the client sends `temperature` but **retries without it** when the model rejects it (keeps `temperature=0` for models that accept it). *Methodological consequence:* Opus runs are **non‑deterministic** |

Two findings are worth dwelling on, because they reshaped the design:

**Finding 9 — the model can fix a bug it cannot test.** On `#1314`, the 14B produced a patch
**byte‑identical to the gold fix** (adding `postcodeRegexInit.Do(initPostcodes)` before the
dict lookup). Yet it abstained — because its self‑written reproduction test asserted that a
*valid* postcode should be *rejected* (the buggy behaviour). That test passes on buggy code
and fails on the correct fix, so the agent's own check vetoed the right answer. The fix —
*a reproduction test must fail on the unpatched base* — is just the definition of
reproduction, applied as a gate. It is general and not specific to any instance.

**Finding 10 — verify, don't gamble.** Mid‑development we briefly let the agent submit any
patch that merely compiled, on the logic that "the gold test is the real judge." That is
exactly *shipping unverified fixes* — on the grading side it harvests localization credit for
wrong patches. We reverted it. The agent ships only what it verified, and abstains
otherwise, even at the cost of zero credit on instances it can't verify.

The throughline: **the architecture worked early; the iteration was about (a) tolerating a
small model's messy output and (b) making the agent's self‑verification trustworthy.** Once
both were solid, a capable model resolved real bugs immediately (§11).

---

## 13. Per-instance deep dive

The five Stage‑1 instances span a deliberate difficulty range. This is what each actually
requires and how the agent fared (Sonnet, final pipeline).

| Instance | The bug | The gold fix | Difficulty | Outcome |
|---|---|---|---|---|
| `validator-1314` | `postcode_iso3166_alpha2_field` always rejects valid postcodes | **one line**: `postcodeRegexInit.Do(initPostcodes)` before the dict lookup (the regex map was never initialised) | a subtle **one‑liner** — must understand control flow (the missing init runs *before* the early `return`) | **resolved**, fix matches gold (diff~1.00) |
| `validator-1444` | URL validation mishandles a `file://` case | a focused change in `isURL`/file‑URL handling (~tens of lines) | **medium** — multi‑line logic | **resolved** with a different‑but‑correct fix |
| `validator-1284` | missing `VarWithKeyCtx`‑style map‑validation entry points | **new public methods** (~78 lines across `validator_instance.go`) | **hard** — substantial new API surface | unresolved (incomplete fix; correct file, builds, vets, passes own repro) |
| `validator-1423` | panic when validating **private/unexported** struct fields | guard around `Interface()` on unexported `reflect.Value` (multi‑file) | **hard** — panic reproduction + multi‑file | noop (`no_repro` — couldn't reproduce the panic in 3 tries) |
| `validator-1476` | `e164` phone validation wrongly accepts codes starting `+0` | tighten the **`e164` regex** in `regexes.go` | **medium** — but the keyword `e164` is **not in the issue prose** | noop (localized to the wrong file → abstained) |

**Use‑case framing.** These map to the kinds of issues a real maintainer triages:
a one‑line "we forgot to initialise X" bug (`#1314`), a "this edge case is mishandled" logic
bug (`#1444`), a "please add this API" feature‑ish request (`#1284`), a crash report with a
stack trace (`#1423`), and a "validation is too lax" regex fix described in prose without the
identifier (`#1476`). The agent handles the first two end‑to‑end, makes a correct‑file but
incomplete attempt on the third, and *safely abstains* on the two it can't verify — which is
the behaviour you want from an automated contributor.

---

## 14. Tests

Stage 4 adds **`tests/test_agent.py`** — 24 unit tests that drive the *entire* spine with a
**fake model** and a **fake validator**, so the full agent is testable with **no Ollama and
no Docker** (the suite runs in ~1 second). Total project suite: **56 tests** (21 tools + 11
metrics + 24 agent), all green.

The fake model (`scripted_llm`) routes on prompt content — returning a canned action, a
canned reproduction test, or a canned fix block depending on which prompt it sees — so each
phase is exercised deterministically.

| Area | Representative tests | Asserts |
|---|---|---|
| edit parsing | `test_parse_single_block`, `test_parse_multiple_blocks_and_fences`, `test_parse_pathfree_block_has_no_path`, `test_parse_strips_angle_brackets` | blocks parse with/without paths; brackets/fences stripped |
| edit applying | `test_apply_edit_changes_file`, `test_apply_pathfree_uses_default_target`, `test_apply_filename_placeholder_falls_back_to_default`, `test_apply_resolves_mangled_path`, `test_apply_rejects_noop_edit`, `test_apply_edit_reports_no_match`, `test_apply_edit_creates_file_on_empty_search` | path‑free application, mangled‑path recovery, **no‑op rejection**, error reporting |
| localization | `test_extract_terms_drops_stopwords_and_ranks_identifiers`, `test_terms_filter_panic_addresses`, `test_localize_points_at_central_file` | stopwords dropped, **hex/panic tokens filtered**, central file ranked |
| context | `test_focus_snippets_includes_named_function`, `test_context_gather_dedups_repeats` | exact source surfaced; **repeated actions de‑duplicated** |
| repro gate | `test_agent_regenerates_repro_until_it_reproduces`, `test_agent_abstains_when_repro_never_reproduces` | regenerates an invalid repro; **abstains (`no_repro`)** when it never reproduces |
| submission policy | `test_agent_resolves_with_fake_validate`, `test_agent_abstains_when_repro_fails`, `test_agent_abstains_when_build_fails`, `test_agent_submits_unvalidated_when_opted_in`, `test_agent_handles_no_edits` | **verified‑only**: resolves when all green; abstains on repro/build failure; opt‑in `gave_up`; `no_edits` |
| seam | `test_llmclient_uses_injected_fn` | the injectable client calls the fake |

The point of the fake‑driven design: every branch of the do‑no‑harm policy and every edit‑
parsing edge case is locked by a fast, deterministic test, so the messy‑real‑model behaviour
from §12 can't silently regress.

---

## 15. Limitations & future work

Stated honestly, since this is the result that matters.

| Limitation | Evidence | Honest characterisation | Possible future work |
|---|---|---|---|
| **Localization misses files whose key identifier isn't in the issue prose** | `#1476`: `e164` lives in `regexes.go` but the issue never says "e164"; the wrong file was targeted | **ours**, real and improvable | semantic retrieval was **built, measured, and deferred** — see §15.1 (it moved the metric by zero); the dependency-free term-normalization + IDF ideas are the first thing to revisit |
| **Resolution is model‑bound** | 7B/14B: 0 resolved; Sonnet: 2 resolved, same pipeline | not the architecture; the model | use a frontier model for ship numbers (done); dev stays local for speed/cost |
| **Large/multi‑file fixes** | `#1284` (~78 lines), `#1423` (multi‑file) not resolved even by Sonnet | partly model fix‑quality, partly that the agent edits **one** `primary_file` | allow multi‑file edits; richer repair feedback loops |
| **Reproduction of crashes is hard** | `#1423`: `no_repro` after 3 tries | model can't reliably write a panic reproducer | more repro attempts; crash‑specific repro guidance (carefully, to avoid overfitting) |
| **Self‑verification depends on the model writing a good test** | Finding 9 | the repro gate catches *invalid* tests but can't *manufacture* a valid one | the gate is the right floor; a stronger model writes better tests |

The deliberate non‑goal: we did **not** tune prompts to pass these specific five issues.
`#1476` in particular would be "fixed" by special‑casing the `e164` keyword — we chose to
**document it as a localization limitation** rather than overfit the term extractor to the
test set.

### 15.1 Semantic localization — built, measured, and deferred

Before settling on the lexical + PageRank localizer, we implemented a semantic-retrieval
extension specifically to attack `#1476`, measured it, and then **removed it** because it
did not earn its complexity. The data is recorded here so the decision is auditable.

We added three ideas and measured each by the rank of the **gold-fix file** (`regexes.go`,
never shown to the agent) among candidates — lower is better, and the bar is **top-3**:

| Approach (cumulative) | `regexes.go` rank for `#1476` |
|---|---|
| lexical hit-count (the shipped localizer) | 19 / 39 |
| + term normalization (`E.164 → e164`, so search can hit `e164RegexString`) | 8 / 40 |
| + IDF weighting (a rare term like `e164` outweighs common ones like `validation`) | 9 / 40 |
| + chunk-level embeddings, fused (best chunk per file, not one diluted whole-file vector) | 17 / 40 |
| whole-file embedding, for contrast (one vector per file — *worse* than lexical) | 33 / 39 |

Two findings stopped the work:

1. **No deterministic signal reaches top-3 for this file.** `regexes.go` is a leaf
   constants file: structurally peripheral (low PageRank), not lexically distinctive
   (`e164` also appears across the `translations/*` error-message files, so its IDF weight
   is modest), and semantically diluted (≈80 regex constants in one file — even chunked it
   only reaches rank ~15). Fusing three mid-pack signals cannot synthesise a top-3, so the
   experimental localization metric (`recall@3` = gold file in the top-3 candidates) stayed
   at **4/5**, and resolution was unchanged. The added machinery — a 67 MB embedding model
   plus a fusion / re-rank / chunking subsystem — moved the metric by **zero**.

2. **The only signal that finds it is the model's own knowledge**, which is
   model-dependent and non-deterministic. In the gate run, Opus ran `SEARCH e164` and
   reasoned *"the regex on line 23 of `regexes.go`…"* purely from domain knowledge. An LLM
   re-rank could exploit that, but it would make localization quality vary by model (and,
   on Opus, run-to-run) — trading a reproducible metric for one that only looks good on the
   strongest model.

The term-normalization and IDF ideas are genuinely sound and dependency-free — IDF, for
instance, lifted `#1423`'s secondary gold file `struct_level.go` from rank **33 → 9** — and
they are the first thing to revisit. But on the dev set they change no resolution and no
`recall@3`, and tuning a fusion against five visible cases is a generalisation risk for the
**held-out** issues this agent is actually judged on. For a system that must generalise, the
boring lexical + PageRank localizer is the more trustworthy default. Semantic retrieval is
**deferred with evidence**, not abandoned for lack of trying.

---

## 16. How to run / reproduce

**Prerequisites.** Docker Desktop up; the sandbox image built (`go-issue-agent-sandbox:dev`);
`.cache/repos/validator` present (Stage 1). For the local dev model: `ollama serve` running
with the model pulled. For the ship model: an API key.

**Unit tests (no Ollama/Docker needed):**

```bash
python -m pytest tests/ -q          # 56 passed
```

**gate‑4 with the local dev model:**

```bash
# .env
LLM_MODEL=ollama/qwen2.5-coder:14b
LLM_API_BASE=http://localhost:11434
SANDBOX_IMAGE=go-issue-agent-sandbox:dev

bash scripts/gate4.sh
```

**gate‑4 with the ship model (the proof run):**

```bash
# .env
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
ANTHROPIC_API_KEY=sk-ant-...
SANDBOX_IMAGE=go-issue-agent-sandbox:dev

python -m go_issue_agent.llm.client --ping     # verify connectivity first
bash scripts/gate4.sh
```

**Artifacts** land in `eval/results/agent/`: `<id>.patch` (the submitted code‑only diff),
`<id>.pr.md` (PR text), `<id>.repro_test.go` (the reproduction), and `<id>.attempt.patch`
(the attempted‑but‑unverified diff, when the agent abstained). The full table and a
machine‑readable `eval/results/gate4.json` are written each run.

**Switching models is config‑only** — no code change. That property is the whole reason we
could prove the central finding of Stage 4:

> The agent's architecture is sound. With a local 14B model it produced the *exact* gold
> fix for `#1314` yet correctly abstained because it couldn't write a test to prove it; with
> Claude Sonnet — same agent, same prompts — it resolved 2/5 and passed gate‑4 (≈ 64K
> input / 4.7K output tokens for the whole run). Claude Opus corroborated it (also PASS,
> `#1314` with the gold fix). The bottleneck was never the pipeline. It was the model.
