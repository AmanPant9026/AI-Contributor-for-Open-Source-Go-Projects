# Agentic Go Issue-Fixer — Master Reference & Execution Plan

> **Purpose.** One self-contained document for the whole project: what we're building, why, the architecture, the tools, the folder layout, the ground-truth stepping stone, the evaluation harness, and the fail-fast execution sequence with gates and rollback. This is the reference we work from during execution.
>
> **Status.** Planning complete; ready to begin execution at Stage 0.

---

## Table of contents

1. [What we're building](#1-what-were-building)
2. [Locked decisions](#2-locked-decisions)
3. [Design philosophy & mental model](#3-design-philosophy--mental-model)
4. [Architecture — the pipeline phases](#4-architecture--the-pipeline-phases)
5. [The tool layer](#5-the-tool-layer)
6. [Model setup](#6-model-setup)
7. [Folder structure](#7-folder-structure)
8. [Ground truth — the stepping stone](#8-ground-truth--the-stepping-stone)
9. [Evaluation harness & metrics](#9-evaluation-harness--metrics)
10. [Execution sequence — stages, gates, checkpoints](#10-execution-sequence--stages-gates-checkpoints)
11. [Revert / rollback architecture](#11-revert--rollback-architecture)
12. [Always-on validators & failure playbook](#12-always-on-validators--failure-playbook)
13. [The dev set (5 instances)](#13-the-dev-set-5-instances)
14. [Worked example — validator #1314](#14-worked-example--validator-1314)
15. [Assignment coverage matrix](#15-assignment-coverage-matrix)
16. [Open items & immediate next step](#16-open-items--immediate-next-step)
17. [References (phase-wise)](#17-references-phase-wise)

---

## 1. What we're building

We are building **two** things, and the order matters:

1. **The agent** — given a GitHub issue from an approved Go repository, it produces a code patch that resolves the issue, plus a PR title and body. *(Headline deliverable.)*
2. **The ruler** — the ground truth (an answer key built from real merged PRs) plus an evaluation harness that **measures** whether a patch is good. *(Built first; it's how we know the agent works and it mirrors how the assignment grades us.)*

**In three sentences:** Given an issue, the system understands it, builds a map of the codebase, lets the LLM navigate that map with tools to localize the fix, plans and writes a minimal patch as diff blocks, and validates the patch by actually compiling, testing, and linting it in a Docker sandbox — repairing it within a bounded number of tries on failure. It then selects the best validated candidate and writes a PR title and body. Reliability comes from the validation gate and the disciplined structure; the "agentic" quality lives in two genuine loops (context-gathering and repair), and the whole trace is logged so it's reviewable.

---

## 2. Locked decisions

| Decision | Choice |
|---|---|
| **Approach** | Tool-using agent with **bounded autonomy** (localize → repair → validate spine). Not fully autonomous, not multi-agent. The word "Agentless" appears nowhere in the deliverable. |
| **Two agentic loops** | Phase 3 (context-gathering) and Phase 7 (repair). |
| **Tools** | We build them ourselves — thin, well-shaped wrappers around `ripgrep`, `tree-sitter`, and the Go toolchain. We own the tool surface; we reuse the robust binaries underneath. |
| **Orchestration** | Hand-rolled loop (transparency). LangGraph allowed only as plumbing — tools/prompts/loop-logic must stay ours. |
| **Extra capabilities** | Artifact caching, idempotent batch eval, parallel candidate validation. **No** mid-run human-in-the-loop (the human reviews the final diff/PR). |
| **Model** | Local on an **A6000 (48 GB)** via Ollama — **Devstral Small 2** (primary; built for coding agents) and **Qwen3.6-27B** (alt), both **Q8**, behind a litellm-backed `llm.py`. One-line swap to hosted GLM-5.1 or proprietary Claude later. |
| **Stack** | Python harness operating on Go repositories. Everything open source; our code MIT/Apache-2.0. |
| **Execution** | All Go build/test/lint runs inside **Docker** (security + reproducibility). |
| **Target repo** | **`go-playground/validator`** (locked). Second config (`cobra`) shipped for extensibility. |
| **Dev set** | 5 verified instances, starting with **#1314**. (See §13.) |
| **Eval strategy** | train/dev/test — tune on the **dev 5**, report on a **held-out 10–20**, measure generalization on the **Multi-SWE-bench Go** pool. |
| **Discipline** | Fail-fast: every stage has a runnable gate; every green gate is a git tag; never proceed on red. |

---

## 3. Design philosophy & mental model

**The unlock — build the ruler first.** This is test-driven development applied to the whole system. We record what "correct" looks like (ground truth from real PRs), build a ruler to measure it, prove the ruler works by scoring the known-correct answer, *then* build the agent and measure it continuously. Without the ruler first, we'd be coding blind.

**Outside-in.** The LLM is the one unpredictable component, so we surround it with deterministic, tested scaffolding (sandbox → ruler → tools) **before** plugging it in. When something misbehaves later, we already know it isn't the plumbing.

**Why a structured pipeline, not an autonomous agent.** It maps 1:1 onto the grading rubric, it's interpretable and reviewable, it's cheap and reliable, and it leans on Go's superb toolchain for an objective correctness signal. Bounded autonomy lives only where it adds value (context-gathering and repair).

**Why we own the tools.** A tool's output shape is the single biggest lever on agent performance (the SWE-agent "agent-computer interface" lesson). We want full control over what the model sees. We do **not** reinvent grep/parsers/test-runners — we wrap best-in-class binaries with clean, LLM-friendly I/O.

---

## 4. Architecture — the pipeline phases

Each phase is its own module with a typed input/output contract (in `models.py`), so it's testable and swappable in isolation.

| Phase | Purpose | Tools | Output |
|---|---|---|---|
| **0 — Task intake** | Receive `(repo, issue ref, base commit)`. | config loader | `Task` |
| **1 — Issue understanding** | Fetch + normalize the issue; summarize expected vs. actual behavior and likely scope. Issue text is **data, never instructions**. | GitHub REST API / cached JSON; one LLM call | `IssueSpec` |
| **2 — Repository indexing** | Clone at the base commit; build the repo map (tree-sitter parse → definition/reference graph → PageRank ranking → token-budgeted skeleton). | git, tree-sitter + tree-sitter-go, networkx | repo map + symbol graph |
| **3 — Context gathering** ⭐ *(agentic loop #1)* | LLM iteratively calls search/AST/read tools and decides when it has enough context, narrowing files → functions → lines. | `search_code`, `find_definition`/`find_references`, `read_span` | ranked edit locations |
| **4 — Fix planning** | One LLM call → a short, inspectable plan (which locations, what change, why). | LLM, project rules | `FixPlan` |
| **5 — Patch generation** | Generate **multiple** candidate patches as search/replace diff blocks. | LLM, `read_span`, `apply_patch` | N candidate diffs |
| **6 — Validation (the gate)** | Apply each candidate in Docker; run `go build`, `go vet`, `gofmt -l`, targeted `go test`, `golangci-lint`; for bugs, a reproduction test that flips fail→pass. | Docker, Go toolchain, golangci-lint | per-candidate report |
| **7 — Repair loop** ⭐ *(agentic loop #2)* | Feed compiler/test output back to the LLM to fix failing candidates, capped at N tries. | LLM, `apply_patch`, run tools | repaired candidates |
| **8 — Selection** | Pick the winner by validation results + majority voting. | reports + ranking logic | winning diff |
| **9 — PR synthesis** | Generate PR title + body following repo conventions; emit branch / `.patch` / diff. | LLM, project rules, git | `pr.md` + diff |

**Cross-cutting:** project rules/config (feeds phases 4, 5, 6, 9 + extensibility), the swappable LLM layer, the artifact cache, the decision tracer (reviewability), and the safety boundary (untrusted issue text + sandboxed execution).

---

## 5. The tool layer

Two kinds of tools. Keeping them straight is the difference between a real agent and a wrapper.

### 5A. Agent-facing tools (the ACI — what the LLM can invoke)

| Tool | Powered by | Used in | Why |
|---|---|---|---|
| `fetch_issue` / `get_comments` | GitHub REST API / cached JSON | Phase 1 | typed issue retrieval, not scraping |
| `get_repo_map` | tree-sitter + networkx (PageRank) | Phase 3 | token-budgeted skeleton so the model knows what exists |
| `search_code` | ripgrep | Phase 3 | grep for identifiers/strings named in the issue |
| `find_definition` / `find_references` / `get_outline` | tree-sitter (Go AST) | Phase 3, 5 | AST-aware navigation beats text search |
| `read_file` / `read_span` | filesystem + tree-sitter boundaries | Phase 3, 5 | read the relevant function, not whole files |
| `apply_patch` | search/replace + unified-diff applier | Phase 5, 7 | small, reviewable diff blocks |
| `run_build` / `run_test` / `run_vet` / `run_fmt` / `run_lint` | Go toolchain in Docker | Phase 6, 7 | the objective truth oracle |

Invocation mechanism: function-calling / a ReAct-style loop (reason → act → observe → repeat).

### 5B. Infrastructure tools (the harness uses these; the LLM never sees them)

| Tool | Where | Why |
|---|---|---|
| Python 3.11+ | orchestrator + glue | the agent ecosystem lives here |
| litellm (behind `llm.py`) | every reasoning step | provider-agnostic; swappable model slot |
| tree-sitter + tree-sitter-go + py-tree-sitter | repo map, AST tools | the parser IDEs use; official Go grammar |
| networkx | repo-map ranking | PageRank over the reference graph |
| ripgrep | `search_code` backend | very fast recursive search |
| Docker | every Go build/test/lint | security boundary + reproducibility |
| git | clone / checkout / branch / diff | the deliverable is a branch/patch/diff |
| Go toolchain + golangci-lint | the validation gate | the correctness signal |
| (optional) FAISS + embeddings | Phase 3 fallback | semantic retrieval if keyword search misses |
| structured JSON trace | wraps the agent | reviewability (a graded dimension) |

---

## 6. Model setup

- **Hardware:** A6000, 48 GB VRAM — comfortably runs a strong coder at near-full quality with headroom for long context.
- **Primary:** **Devstral Small 2** at Q8 (Mistral built the Devstral line specifically for coding agents and single-GPU use).
- **Alternative:** **Qwen3.6-27B** at Q8 (broad, strong general coder).
- **Quantization:** Q8 (near-lossless; we have the VRAM, so we don't drop to Q4).
- **Decision rule:** pull both; let the eval harness pick the winner on real issues ("measure, don't guess").
- **Swappability:** model is a config string behind the litellm-backed `llm.py`. Switching to hosted GLM-5.1 or a proprietary Claude later is a one-line change.
- **Why a local non-frontier model is enough:** the harness matters more than the model for this task; a well-built harness on a mid open-weight model beats a sloppy harness on a frontier model — and it's free, private, and needs no API key during development.

---

## 7. Folder structure

```
go-issue-agent/
├── README.md                      # what it is, quickstart, how each stage works, eval table, limits
├── LICENSE                        # MIT or Apache-2.0
├── pyproject.toml                 # project metadata, deps, console entry point
├── requirements.lock              # fully pinned deps for reproducibility
├── .env.example                   # model endpoint, optional GitHub token
├── .gitignore
├── .pre-commit-config.yaml        # fast checks so red never lands
├── Makefile                       # setup / run / eval / lint / test shortcuts
├── Dockerfile                     # sandbox image: pinned Go + golangci-lint + git
│
├── config/                        # per-repo configs — the extensibility seam
│   ├── default.yaml               # global defaults (candidates, max_repair_iters, model)
│   ├── validator.yaml             # build/test/lint cmds + conventions for validator
│   └── cobra.yaml                 # second config — proves extensibility
│
├── prompts/                       # versioned prompt templates (a named deliverable)
│   ├── issue_understanding.md
│   ├── localization.md
│   ├── planning.md
│   ├── patch_generation.md
│   ├── repair.md
│   └── pr_synthesis.md
│
├── src/go_issue_agent/
│   ├── cli.py                     # entry point: `agent run --config ... --issue ...`
│   ├── orchestrator.py            # the pipeline driver (hand-rolled loop)
│   ├── config.py                  # config load + validation (pydantic)
│   ├── models.py                  # typed contracts: Task, IssueSpec, FixPlan, Candidate, Report, Result
│   │
│   ├── llm/
│   │   ├── client.py              # litellm wrapper: retries, token/cost accounting
│   │   └── toolcall.py            # function-calling / tool-use loop helper
│   │
│   ├── tools/                     # AGENT-FACING tools (the ACI) — we build these
│   │   ├── base.py                # Tool interface + registry + JSON schema
│   │   ├── repo_map.py            # repo map (tree-sitter + PageRank)
│   │   ├── search.py              # search_code (ripgrep)
│   │   ├── ast_nav.py             # find_definition / find_references / outline
│   │   ├── fileio.py              # read_file / read_span / list_dir
│   │   ├── patch.py               # apply_patch (search-replace + unified diff)
│   │   └── go_tools.py            # run_build / run_test / run_vet / run_fmt / run_lint
│   │
│   ├── phases/                    # each pipeline stage as a module
│   │   ├── ingest.py              # Phase 1
│   │   ├── index.py               # Phase 2
│   │   ├── localize.py            # Phase 3  (agentic context loop)
│   │   ├── plan.py                # Phase 4
│   │   ├── generate.py            # Phase 5
│   │   ├── validate.py            # Phase 6
│   │   ├── repair.py              # Phase 7  (agentic repair loop)
│   │   ├── select.py              # Phase 8
│   │   └── pr.py                  # Phase 9
│   │
│   ├── indexing/
│   │   ├── parser.py              # tree-sitter Go parsing
│   │   └── graph.py               # symbol/reference graph + PageRank
│   │
│   ├── sandbox/
│   │   ├── runner.py              # run command in container, capture/parse output
│   │   └── repo.py                # git clone / checkout / branch / diff
│   │
│   ├── cache/
│   │   └── store.py               # file/JSON cache: repo map, LLM responses, candidate results
│   │
│   └── observability/
│       └── tracer.py              # structured JSON trace of tool calls + decisions
│
├── eval/                          # the ruler — mirrors the grader
│   ├── run_eval.py                # run pipeline/patch over tasks, compute metrics
│   ├── metrics.py                 # fail→pass, localization recall/precision, gates, diff overlap
│   ├── tasks/                     # GROUND TRUTH — (issue, gold-PR) instances as JSON
│   │   ├── validator-1314.json
│   │   ├── validator-1476.json
│   │   ├── validator-1444.json
│   │   ├── validator-1423.json
│   │   ├── validator-1284.json
│   │   └── heldout/               # the held-out 10–20 (added in Stage 6)
│   └── results/
│       └── baseline.json          # saved baseline; the regression alarm
│
├── outputs/                       # per-run artifacts; sample outputs committed here
│   └── <repo>-<issue>-<runid>/{patch.diff, pr.md, run.log, trace.json}
│
├── tests/                         # tests for the harness itself
│   ├── test_tools.py
│   ├── test_patch.py
│   ├── test_metrics.py
│   ├── test_phases.py
│   └── fixtures/
│
├── docs/
│   ├── architecture.md            # this document
│   ├── ground_truth.md            # how to build a GT instance (the recipe)
│   ├── evaluation.md              # metrics + how to read the eval table
│   ├── DECISIONS.md               # ADRs: bounded autonomy, own tools, local model, hand-rolled vs langgraph
│   └── adding_a_repo.md           # extensibility guide
│
└── scripts/
    ├── setup.sh                   # install deps, build docker image
    ├── pull_models.sh             # ollama pull devstral / qwen
    └── check_env.sh               # the Stage-0 gate
```

**The professionalism is in the separation:** agent-facing `tools/` vs harness internals (`indexing/`, `sandbox/`, `cache/`, `observability/`) vs pipeline `phases/` vs the swappable `llm/` layer vs the ruler (`eval/`) vs the visible deliverables (`config/`, `prompts/`, `outputs/`, `docs/`). `models.py` holds the typed contracts that connect every phase.

---

## 8. Ground truth — the stepping stone

Ground truth is the **answer key** (data); the eval harness is the **ruler** (code that reads the answer key to score a patch). They are separate, and ground truth comes first.

### 8A. The instance schema (standard SWE-bench format)

```json
{
  "instance_id": "go-playground__validator-1314",
  "repo": "go-playground/validator",
  "base_commit": "<parent of fix #1359; e.g. tag v10.24.0, the release before the fix>",
  "problem_statement": "<issue #1314 body, verbatim>",
  "patch": "<gold CODE diff — the fix, no test files>",
  "test_patch": "<gold TEST diff, or your reproduction test>",
  "FAIL_TO_PASS": ["TestPostcodeFieldRegression"],
  "PASS_TO_PASS": ["TestSomeExistingThing", "..."],
  "go_version": "1.22"
}
```

> **Hidden from the agent.** The agent is given **only** `problem_statement` + the repo at `base_commit`. It never sees `patch`, `test_patch`, `FAIL_TO_PASS`, or `PASS_TO_PASS` — those are the key the ruler grades against.

### 8B. Per-instance recipe (run 5×)

1. Pick a **confirmed-merged** bug-fix PR that closed an issue.
2. Pull facts: `gh pr view <N> --json title,body,mergeCommit,files,closingIssuesReferences`; `gh pr diff <N>` (gold diff); `gh issue view <issue> --json body` (`problem_statement`).
3. `base_commit` = `git rev-parse <mergeCommit>^1` (parent of the merge), or the release tag just before the fix.
4. Split the gold diff: `patch` = non-`*_test.go` hunks; `test_patch` = `*_test.go` hunks. **If the PR shipped no test, author a reproduction test from the issue's code sample — that becomes `test_patch`.**
5. **Verify (the discipline that makes the instance trustworthy):** check out `base_commit` in the sandbox → apply *only* `test_patch` → run the named test → it must **FAIL** (bug reproduced; this is `FAIL_TO_PASS`). Then apply `patch` → it must **PASS**. Record a few existing passing tests as `PASS_TO_PASS`.
6. Save the JSON to `eval/tasks/`.

### 8C. The gate for this stage

For **every** instance: gold patch → `FAIL_TO_PASS` flips to PASS (`resolved=1`); empty/no-op patch → stays FAIL (`resolved=0`). An instance that can't be made to fail at base isn't capturing the bug → **drop it (fail fast on bad instances).**

---

## 9. Evaluation harness & metrics

The harness takes a task + a candidate patch, applies it in the sandbox, runs the tests, and computes metrics. It mirrors the assignment's five validation axes exactly.

| Metric | Maps to grader axis | Notes |
|---|---|---|
| **Fail-to-pass resolution rate** | "produces relevant code changes" | The headline, objective signal (= pass@1). Behavioral, not appearance-based. |
| **Localization recall / precision** | "identifies the right files" | Did we touch the files the gold PR touched? |
| **Regression / pass-to-pass** | part of "validation" | Existing passing tests must still pass — proves we didn't break anything. |
| **Build / vet / fmt / lint gates** | "follows conventions" + "runs validation" | Binary gates via the Go toolchain. |
| **Diff similarity** | hint toward "relevant changes" | *Secondary* — a correct fix can look different; never optimize for it over resolution. |
| **PR-summary quality** | "reasonable PR summary" | Qualitative; eyeball or optional LLM rating. |
| (optional) tokens/cost, repair iters | efficiency | reported, not gated |

**Eval strategy (train/dev/test):**
- **Dev set (5, hand-built):** the set we *tune* against → optimistic scores; for fast iteration.
- **Held-out (10–20, same repo):** never tuned on → the honest in-distribution number.
- **Multi-SWE-bench Go pool (other repos):** never tuned on, unseen repos → the generalization number.

`baseline.json` stores the dev-set result; after any change we re-run and diff against it (`make eval-dev`) to catch regressions, with `git bisect` to find the culprit.

---

## 10. Execution sequence — stages, gates, checkpoints

> **The working cadence (every stage):** state the gate as a runnable check → build the smallest increment → run the gate → **green** = commit + tag a checkpoint + proceed; **red** = quick-fix or **revert to the last green tag**. Never build on red. Cheap checks before expensive; smoke tests before full runs.
>
> **Dependency chain:** `bedrock → ground truth → ruler → tools → agent loop → reliability → polish`.

### Stage 0 — Bedrock
- **Do:** repo skeleton, pinned Dockerfile (Go + golangci-lint), `requirements.lock`, `llm.py` wired to Ollama, the sandbox runner (clone/checkout a commit, run a command in the container, parse pass/fail).
- **Gate:** `scripts/check_env.sh` confirms the image builds, `docker run <img> go version` prints the pinned Go, Ollama answers a ping from Devstral; check out `validator@v10.24.0` and run `go test ./...` in the box and parse it — **and** run on a deliberately broken checkout to confirm we detect *failure*, not just success.
- **Pass when:** both passing and broken cases parse correctly. **Checkpoint:** `gate-0`.

### Stage 1 — Ground truth (the stepping stone) ⭐
- **Do:** build the 5 `eval/tasks/validator-*.json` instances via the §8B recipe.
- **Gate:** for every instance, gold patch → `resolved=1`; empty patch → `resolved=0` (§8C).
- **Pass when:** all 5 satisfy gold-passes / empty-fails. **Checkpoint:** `gate-gt`.

### Stage 2 — The ruler (eval harness)
- **Do:** `metrics.py` + `run_eval.py`.
- **Gate:** run the harness over all GT instances → gold patches all score `resolved=1`, recall `1.0`, gates green; empty patches all `resolved=0`. Save `baseline.json`. *(If this fails, the ruler is broken — more urgent than anything downstream.)*
- **Pass when:** gold perfect, empty zero, through the harness. **Checkpoint:** `gate-2`.

### Stage 3 — The agent's hands (tools)
- **Do:** `repo_map`, `search_code`, `ast_nav`, `read_span`, `apply_patch`, `go_tools` — each with a unit test on a fixture.
- **Gate:** `search_code("isPostcodeByIso3166Alpha2Field")` returns the right file/line; `apply_patch` applies a known diff and the result compiles; `repo_map` stays within the token budget and includes `baked_in.go`. All tool unit tests pass.
- **Pass when:** the tool suite is green (a tool bug can no longer masquerade as an LLM failure). **Checkpoint:** `gate-3`.

### Stage 4 — The agent's brain (LLM loop, happy path)
- **Do:** wire phases end to end — ingest → index → localize → plan → generate (single candidate) → validate → select → PR. No repair yet. Smoke-test on #1314.
- **Gate (pipeline integrity, *not* model success):** a full run on #1314 completes without crashing, emits a well-formed diff, the harness scores it, and a trace is written. Bar = "ran clean and produced a scored, well-formed patch"; actually resolving #1314 is the aspiration, not the gate.
- **Pass when:** the pipeline runs clean end-to-end and produces a scored artifact + trace. **Checkpoint:** `gate-4`.

### Stage 5 — Reliability
- **Do:** bounded repair loop, multi-candidate + test-based selection/majority vote, artifact cache, parallel candidate validation, decision tracer.
- **Gate:** inject a broken patch → repair engages and **provably stops at N** (no infinite loop); selection picks a validated candidate when one exists; a re-run hits the cache (zero LLM calls for unchanged inputs); the trace lists tool calls.
- **Pass when:** each robustness feature has a passing check and the loop is bounded. **Checkpoint:** `gate-5`.

### Stage 6 — Scale, polish, deliverable
- **Do:** run over all 5 dev instances, expand toward the held-out 10–20, write README + committed sample outputs + `DECISIONS.md` + extensibility doc, populate `cobra.yaml`, clean-room reproducibility check, optionally a few Multi-SWE-bench Go instances for the generalization number.
- **Gate:** dev-set eval produces a results table (the honest baseline); a **clean-room run** (fresh clone → `make setup && make run ISSUE=1314`) produces output with nothing pre-installed; the replay-from-cache mode works without a GPU.
- **Pass when:** a reviewer can reproduce from the README alone and the eval table is honest. **Checkpoint:** `gate-6` / tag `v1.0`.

---

## 11. Revert / rollback architecture

Nothing we do should be unrecoverable. Seven layers:

1. **Git as the time machine.** `main` stays always-green; one branch per stage; **every passed gate is a tag** → rollback is `git reset --hard gate-N`. Small, frequent commits.
2. **The agent can't corrupt anything.** It only ever edits a *throwaway clone of the target repo at the base commit, inside Docker*. The host is never touched; a catastrophic run is undone by deleting a container.
3. **Per-run isolation.** Each run has its own workspace + `outputs/<repo>-<issue>-<runid>/`. Discarding a run = deleting a folder. Runs are idempotent.
4. **The cache is a safety net.** Intermediate artifacts (repo map, LLM responses, validation results) are keyed by inputs, so reverting *code* never forces re-paying for expensive LLM/Docker work, and re-runs are deterministic.
5. **Pinned environment.** Dockerfile pins Go + golangci-lint; `requirements.lock` pins Python; the model is pinned by Ollama tag. Green stays green because the environment can't drift.
6. **Versioned config + prompts.** A regression from a prompt tweak reverts exactly like a code regression.
7. **The eval is the regression alarm.** `baseline.json` + `make eval-dev` diff gives an objective improve/regress signal; `git bisect` finds the culprit commit.

---

## 12. Always-on validators & failure playbook

**Run constantly, not just at milestones:**
- `make test` — unit tests for tools, patch application, and metrics.
- `make eval-dev` — the 5-instance dev eval, **diffed against `baseline.json`**.
- `make lint` / `make fmt` — keep the harness itself clean.
- A pre-commit hook running the fast checks so red never gets committed.

**Failure playbook:**

| Symptom | Likely cause | Action |
|---|---|---|
| A gate goes red right after a change | the change | revert to the last `gate-` tag; reintroduce in smaller pieces |
| Dev-eval score drops vs baseline | a regression | `git bisect` to the offending commit; revert it |
| Agent run crashes | loop/prompt (tools are tested) | read the trace; isolate the failing phase |
| Ruler gives a weird score | the harness, not the agent | re-run the Stage-1 gold/empty check; fix the ruler first |
| Docker/env misbehaves | environment drift | rebuild from the pinned Dockerfile; never debug a drifted env |

---

## 13. The dev set (5 instances)

All confirmed merged (each shipped in a tagged release), chosen to spread across **fix-shapes, files, and reproduction paths**.

| # | PR / issue | Fix shape | File(s) | Reproduction path |
|---|---|---|---|---|
| 1 | issue **#1314** → fix **#1359** | missing initialization (one-liner) | `baked_in.go` | **no test in PR → we write the repro** |
| 2 | **#1476** (e164 rejects `+0`) | boundary fix spanning a regex + its use | `baked_in.go` + `regexes.go` | has test; **2 code files → multi-file handling** |
| 3 | **#1444** (`file://` must fail `url`) | string/URL parsing logic | `baked_in.go` | has test; clean fail→pass |
| 4 | **#1423** (private-field panic) | panic on reflection | `validator.go` (engine) | has test; **different file** |
| 5 | **#1284** (missing map-error keys) | incorrect error output | `validator.go` / `errors.go` | has test; **different file** |

Coverage: five distinct shapes, three areas of the codebase (registry, traversal engine, regex/error helpers), both reproduction paths, and one genuine multi-code-file fix (#1476). Optional diversity swap-ins (also confirmed-merged): **#1391** (translations subpackage nil-deref) or **#1507** (tag-parsing/cache panic). File attributions for #1423/#1284 are confirmed during triage via `gh pr view <N> --json files`.

---

## 14. Worked example — validator #1314

**The bug.** The tag `postcode_iso3166_alpha2_field` maps to `isPostcodeByIso3166Alpha2Field` in `baked_in.go`. A refactor (PR #1270, released v10.21.0) dropped the lazy-init call `postcodeRegexInit.Do(initPostcodes)`. Without it, `postCodeRegexDict` is empty → every postcode fails. The sibling `isPostcodeByIso3166Alpha2` still had the init call — that sibling is the localization tell. Fixed in #1359 (v10.25.0).

**The pipeline on this issue:**
- **P0/P1:** Task = `(validator, #1314, base v10.24.0)`. Extract expected (valid US "12345" should pass), actual (fails on the tag), clue (regression in v10.21.0), and the searchable tag name.
- **P2:** repo map surfaces `baked_in.go` as high-centrality.
- **P3 (agentic):** `search_code("postcode_iso3166_alpha2_field")` → the handler; `find_definition` → the function; read it and the sibling → the `_field` variant is missing the init call.
- **P4:** plan = add `postcodeRegexInit.Do(initPostcodes)` before the dict lookup, mirroring the sibling. One line, no API change.
- **P5:** a search/replace diff adding that line.
- **P6:** synthesize a reproduction test from the issue sample; on base it FAILS; with the patch it PASSES; build/vet/fmt/lint all green.
- **P7:** not needed (compiled + passed first try); the loop would engage on failure.
- **P8/P9:** select the validated candidate; emit `pr.md` ("Fixes #1314…") + diff.
- **Eval vs gold #1359:** localization recall 1.0, fail-to-pass resolved, diff similarity high, gates green — a clean win on every axis.

---

## 15. Assignment coverage matrix

| Assignment requirement | Where covered | ✓ |
|---|---|---|
| Agentic platform for Go issues, production-quality changes | pipeline + Go validation gate | ✓ |
| "System you built, not a thin wrapper" | hand-rolled orchestration + own tools | ✓ |
| Evaluate: agents / tools / repo understanding / code modification / validation / extensibility | two loops; own tool layer; repo map + graph; diff-block patching + repair; Docker gate; config-driven design | ✓ |
| Choose one approved repo; small/medium issue; avoid architectural/security/rewrite/unclear | validator; 5 small confirmed-merged bug fixes; risky classes excluded in triage | ✓ |
| Inspect repo / understand issue / identify files / plan / modify / run checks / PR title+body | Phases 2 / 1 / 3 / 4 / 5+7 / 6 / 9 | ✓ |
| PR opening optional; branch/patch/diff + summary sufficient | branch + `.patch` + `pr.md` | ✓ |
| Architecture freedom (rules, repo maps, code search, embeddings, project rules, tool agents) | repo map + code search + project rules + tool agent; embeddings optional | ✓ |
| Validate by comparing to accepted PRs on five axes | ground truth + harness mirror it exactly | ✓ |
| Deliverables: system, README, configs, prompts, rules, indexes, sample outputs; easy to run/review | full `src/` + README + Dockerfile + `config/` + `prompts/` + committed `outputs/` + `eval/results/` + `docs/` | ✓ |
| Not production-grade; simple/reliable > complex | governing philosophy; fail-fast gates | ✓ |

**Watch-items (covered; keep tight):** (1) grader-machine reproducibility — lock the litellm swap + committed sample outputs + replay-from-cache mode so no GPU is needed to review; (2) populate the **cobra** config so we score on extensibility and survive a different approved repo; (3) optionally commit a prebuilt repo-map index as the "indexes" artifact.

---

## 16. Open items & immediate next step

**Open (non-blocking):**
1. Orchestration: hand-rolled (current lean) vs LangGraph — doesn't block scaffolding.
2. Devstral vs Qwen — decided empirically during Stage 6 (we pull both).

**Immediate next step:** begin **Stage 0 (bedrock)** + the first ground-truth instance **`validator-1314.json`**, then run `gate-0` and `gate-gt`.

---

## 17. References (phase-wise)

**Orientation (read first):**
- *Agentic Software Issue Resolution with LLMs: A Survey* (Dec 2025) — https://arxiv.org/abs/2512.22256
- *LLM-Based Agents for Software Engineering: A Survey* — https://arxiv.org/abs/2409.02977 (paper list: https://github.com/FudanSELab/Agent4SE-Paper-List)
- **Multi-SWE-bench** (multilingual, **includes Go**) — https://arxiv.org/abs/2504.02605 (code: https://github.com/multi-swe-bench/multi-swe-bench)

**Phase 1 (issue understanding):** SWE-bench — https://arxiv.org/abs/2310.06770 · SpecRover — https://arxiv.org/abs/2408.02232
**Phase 2 (indexing / repo map):** Aider repo map — https://aider.chat/2023/10/22/repomap.html · RepoGraph — https://arxiv.org/abs/2410.14684
**Phase 3 (localization, agentic loop #1):** Agentless — https://arxiv.org/abs/2407.01489 · AutoCodeRover — https://arxiv.org/abs/2404.05427 · LocAgent — https://arxiv.org/abs/2503.09089
**Phase 4 (planning):** SpecRover (above) · ReAct — https://arxiv.org/abs/2210.03629
**Phase 5 (patch generation):** Agentless (above)
**Phase 6 (validation):** SWE-bench (above) · Multi-SWE-bench (above; Go test-status nuance)
**Phase 7 (repair loop, agentic loop #2):** Reflexion — https://arxiv.org/abs/2303.11366 · Self-Refine — https://arxiv.org/abs/2303.17651 · ReAct (above)
**Phase 8 (selection):** Agentless (above)
**Phase 9 (PR synthesis):** SpecRover (above)
**Contrast (what we chose not to do):** SWE-agent — https://arxiv.org/abs/2405.15793 · OpenHands — https://arxiv.org/abs/2407.16741
**Background:** Toolformer — https://arxiv.org/abs/2302.04761 · RepoCoder — https://arxiv.org/abs/2303.12570

**SWE-bench family (for the eval):** original (2310.06770) · Verified (https://www.swebench.com/verified.html) · Multilingual (https://www.swebench.com/multilingual.html) · Multi-SWE-bench / Go (2504.02605) · Multimodal (https://arxiv.org/abs/2410.03859)

**Tooling docs:** tree-sitter-go (https://github.com/tree-sitter/tree-sitter-go) · py-tree-sitter (https://github.com/tree-sitter/py-tree-sitter) · golangci-lint (https://golangci-lint.run) · Go (https://go.dev/doc/) · MCP (https://modelcontextprotocol.io) · Ollama + litellm + Devstral/Qwen model cards (Hugging Face)

---

*End of master reference. Next action: Stage 0 + `validator-1314.json`.*
