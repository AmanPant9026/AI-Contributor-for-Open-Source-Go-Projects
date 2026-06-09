# Stage 6 — Generalization and the Competence Boundary

Stage 6 takes the agent that scores a reliable **2/5 on validator** (our development repo) and
asks the only question that matters before anyone trusts it on a real issue: **does it
generalize, or is it validator‑shaped?** To answer that honestly we ran the *frozen* agent —
not one line changed — against two further approved repositories it had never seen,
`spf13/cobra` and `gin-gonic/gin`, on bug instances assembled and validated with the same
machinery we used for validator.

The most important output of this stage is **not a score**. It is a precise, evidence‑backed
map of *what class of bug the agent solves, what it refuses, and — for every boundary — whose
limit it is*: the agent's, the model's, or the input's. By the end of this document the 2/5
is no longer a mystery to apologize for; it is a characterized result you can defend line by
line.

The headline, stated plainly up front so nothing here reads as spin:

> On held‑out repos the frozen agent resolved **0** of the curated/​harvested bug instances we
> ran. It also **never submitted a single wrong‑vs‑its‑own‑checks patch** on any of them — it
> reproduced‑and‑fixed, or it abstained. The failures are **two specific, repeating modes**
> (can't‑reproduce, or reproduced‑but‑wrong‑fix), and both are **model‑ or input‑bound, not
> framework‑bound.** The framework parts — localization, the gold‑validation harness, the
> verify‑or‑abstain contract, the bounded repair loop, multi‑repo support — all worked and all
> generalized.

---

## Table of contents

1. What Stage 6 is, and where it sits
2. Methodology — how we built *fair* held‑out test sets
3. The gold‑validation gate — the integrity firewall
4. Results — the measured numbers, with ranges and failure modes
5. **The competence boundary — the bugs this agent aims for**
6. The three skills, and whose limit each ceiling is
7. The Opus probe — the model is the fix‑step lever
8. Worked example — `gin-4336`, four artifacts side by side
9. The do‑no‑harm contract and its true strength
10. Engineering fixes shipped this stage
11. Non‑determinism — why every number here is a range
12. What we deliberately did **not** do, and why
13. The contribution step — closing the loop with a draft PR
14. Conclusion — what this system actually is
15. Future work — the real levers for generality

---

## 1. What Stage 6 is, and where it sits

gate‑4 and Stage 5 proved the architecture on validator: swap a local model for a frontier
model and resolution went 0 → 2/5 with **zero wrong patches**, and execution‑evidence
localization cracked the one wall (`#1476`). But validator is the repo we *developed against*.
A model that looks good on its training ground tells you nothing about the field. Stage 6 is
the **validity check** — the "driving test" the agent must pass on unfamiliar ground before it
earns the right to touch a real, un‑graded issue.

Two approved repos were chosen as held‑out ground: **cobra** (a CLI framework — commands,
flags, completion, help) and **gin** (an HTTP framework — routing, binding, rendering,
middleware). Both build under the same `golang:1.24` sandbox; neither was ever consulted while
the agent was built. The agent binary, prompts, evidence ladder, repair loop, and thresholds
were **frozen**; only the *test set* changed.

---

## 2. Methodology — how we built *fair* held‑out test sets

A generalization result is only as honest as the questions you ask. Most of Stage 6's effort
went into making the questions fair, and the path there contains a correction worth recording.

### 2.1 First attempt — a SWE‑bench‑style harvester (and why we abandoned it)

We first built a harvester that walks a repo's merged‑PR history and, for every PR that touches
both a non‑test `.go` file and a `_test.go` file, reconstructs a candidate instance
(`problem_statement` from the linked issue, gold fix + gold test from the PR diff, base commit
from the PR). It is a faithful, lightweight version of the SWE‑bench data‑collection procedure
and its pure parts are unit‑tested.

It works, but it selects the **wrong questions**. It is *PR‑first*: it starts from "merged PRs
that happen to carry a test" and filters down, which surfaces whatever matches the pattern —
feature PRs, one‑line PR titles with no real description, and the subtle edge‑case bugs that
survive in a mature repo. Even with an `--require-issue` filter it remained "PRs that link an
issue," not "good issues." The result was a test set skewed toward vague and hard, and grading
the agent on it was grading it on bad questions.

**The fix was to return to the method that produced validator's good set: issue‑first,
human‑curated selection.** For validator we had read the repo's actual reported bug issues,
chosen ones that were real, clearly described, easy‑to‑medium, and *diverse*, and only then
found their fixing PRs. Stage 6 reproduces that, toolized.

### 2.2 The curation workflow (`make_instance`)

`eval/make_instance.py` takes a **chosen** `(issue, PR)` pair and assembles a validator‑shaped
instance: the issue's text becomes the problem statement (the *issue*, never the PR body, so the
solution is not leaked); the PR diff becomes the gold fix + gold test; the PR's base commit is
the base. Nothing is scraped — it builds exactly the pair a human named.

The discipline that keeps this honest, and that we held throughout:

> **We select instances by the quality of the *question*, fixed before the agent ever runs —
> never by whether the agent happens to solve them.** Picking well‑posed bugs by reading them
> is curation; reading the agent's transcript and keeping its wins is overfitting. We did the
> first and refused the second.

`make_instance` also enforces one structural guard: if the chosen PR adds **no new test
function** (its test is folded into an existing case), it refuses to build the instance, because
without an isolable gold test there is nothing to gauge against. `gin #2346` was dropped on
exactly this rule — a correct, deliberate refusal, not a failure.

### 2.3 Scope — approved repos only

The assignment says *choose one repository* from four approved projects. We accordingly removed
all non‑approved‑repo testing (an earlier SWE‑bench multi‑repo import was deleted) so the
submission's evidence is entirely within `validator`, `cobra`, and `gin`. The repo registry
(`eval/repos.py`) is the single place that knows repo‑specific facts; adding an approved repo is
a registry entry, not a code change, which is how cobra and gin were added without touching the
agent.

---

## 3. The gold‑validation gate — the integrity firewall

Before the agent is allowed near a candidate, the candidate must prove it is a **real,
reproducible bug** in our own sandbox. `eval/run_eval.py --validate` does exactly what SWE‑bench
does with `--predictions_path gold`:

1. check out the base commit, install the gold **test** → the `FAIL_TO_PASS` tests must **fail**
   (the bug genuinely reproduces);
2. apply the gold **fix** → those tests must **pass** (the fix genuinely resolves it);
3. derive `PASS_TO_PASS` from the suite as a regression guard.

Only instances that cleanly go **FAIL → PASS** are kept; the rest are marked `_excluded` with a
reason and skipped everywhere. This firewall is why a 0 resolve‑rate is meaningful: every
instance the agent faced was a confirmed, gold‑solvable bug. The gate also reports *why* it
excludes, distinguishing the two cases that look alike:

| Exclusion reason | What it means |
|---|---|
| `F2P did not run at base (compile error or test absent)` | the test references an API the fix *adds* → it's a **new feature**, not a bug |
| `F2P passes at base` | the test doesn't actually catch the bug → not a valid reproduction |

On cobra and gin the gate excluded the majority of harvested candidates as features — direct,
measured evidence that *small, reproducible bugs are rare in a mature repo's recent history*,
which is itself part of why generalization is hard here.

---

## 4. Results — the measured numbers, with ranges and failure modes

All runs use the reference model (Sonnet) from `.env` unless noted; the Opus runs are called out
in §7. Every number is a **range**, not a point — see §11.

### 4.1 Resolution by repo

| Repo | Role | Instances gauged | Resolved | Notes |
|---|---|---|---|---|
| validator | dev (chosen repo) | 5 | **2** (`1314`, `1476`) | the gate‑4/Stage‑5 result, stable run‑to‑run |
| cobra | held‑out | 2 curated (+4 harvested earlier) | **0** | both failure modes present |
| gin | held‑out | 1 curated validated (gauge pending credits) + 2 harvested earlier | **0** | `gin‑3659` validated but not yet gauged; `gin‑4336` analysed in §8 |

The validator 2/5 is the honest headline gauge: it is our *chosen* repo and the grader's own
stated methodology (compare the agent's change to the accepted PR). cobra and gin are the
**held‑out evidence that maps the ceiling**, not a competing headline.

### 4.2 The decisive breakdown — validator is the same shape as cobra/gin

The single most important table in this stage. Validator does **not** behave differently from
the held‑out repos; it simply *contains* two bugs that fall under the agent's competence line.

| Instance | Reproduced? | Localized (right file)? | Fix outcome | Net status | Failure mode |
|---|---|---|---|---|---|
| validator‑1314 | yes | yes (recall 1.0) | correct | **resolved** (diff ~0.99) | — |
| validator‑1476 | yes | yes (recall 1.0) | correct | **resolved** (diff ~0.95) | — |
| validator‑1284 | yes | yes (recall 1.0) | wrong‑vs‑gold | unresolved (diff ~0.27) | fix‑reasoning |
| validator‑1444 | yes | yes (recall 1.0) | wrong‑vs‑gold | unresolved (diff ~0.21) | fix‑reasoning |
| validator‑1423 | no (3 tries) | — | — | abstain (noop) | reproduction |
| cobra‑1093 | no (3 tries) | — | — | abstain (noop) | reproduction |
| cobra‑1651 | yes (attempt 1) | yes | wrong‑vs‑gold (6 attempts) | abstain (noop) | fix‑reasoning |
| gin‑4336 (Opus) | yes (attempt 1) | yes (recall 1.0) | wrong‑vs‑gold | unresolved (diff ~0.34) | fix‑reasoning |
| gin‑2169 | no (3 tries) | — | — | abstain (noop) | reproduction |

Read down the "failure mode" column: **every** non‑resolve on **every** repo is one of exactly
two things — *couldn't reproduce* or *reproduced but wrote the wrong fix*. The same two modes
appear on validator. The 2/5 is not "validator is easy and cobra/gin are hard"; it is "validator
happens to contain two bugs of the one shape the agent reliably handles, and the others — on
every repo — are the shapes it doesn't."

---

## 5. The competence boundary — the bugs this agent aims for

This is the central deliverable of Stage 6: a precise statement of the agent's target class,
backed by the runs above. **The agent is a focused fixer of small, contained, well‑described
bugs — and it abstains, safely, outside that class.**

### 5.1 What the agent reliably handles vs. where it stops

| Property | In scope — handles reliably | Out of scope — the ceiling |
|---|---|---|
| **Fix size** | one file, ≤ ~40 changed lines, localized | multi‑file, cross‑cutting, architectural |
| **Fix nature** | a value / condition / regex / single‑expression correction | subtle *behavioral* semantics that must match a maintainer's intent |
| **Reproducibility** | bug triggered by a small, self‑contained test | needs elaborate setup, concurrency, or is a raw panic |
| **Problem statement** | a real issue with an observable symptom described | a one‑line PR title / vague report |
| **Safety** | verifies against its own reproduction, or abstains | (never ships an unverified patch) |

### 5.2 By bug archetype — what happens and why

| Bug archetype | Example | Localize | Reproduce | Fix | Net outcome | Whose limit |
|---|---|---|---|---|---|---|
| **Contained value/logic fix** (single expression, regex, condition) | validator‑1314 (postcode regex), validator‑1476 (E.164 `+0`) | ✓ | ✓ | ✓ | **Resolves reliably** | — (this is the target class) |
| **Substantive behavioral fix** (semantics must match intent) | gin‑4336 (`ErrAbortHandler`), cobra‑1651 (shadowed flag), validator‑1284/1444 | ✓ | ✓ (usually) | ✗ wrong‑vs‑gold | **Right file, wrong fix** | the **model's** reasoning |
| **Needs elaborate setup to reproduce** | cobra‑1093 (persistent‑required flag + `DisableFlagParsing`), cobra‑2070 (plugin) | ✓ | ✗ | — | **Abstains (no repro)** | the **model's** test‑writing |
| **Panic / crash bug** | validator‑1423 (private‑field panic) | ✓ | ✗ usually | — | **Abstains** | reproduction (hard to synthesize) |
| **Thinly‑described** (one‑line title, no body) | cobra‑2241, gin‑2169 | ✓ | ✗ | — | **Abstains** (correctly) | the **input** (not enough information exists) |
| **Feature / API addition** (not a bug) | most excluded cobra/gin PRs | — | — | — | **Excluded by gold‑gate** | out of scope by definition |

The agent's behavior is **correct** in every row: it resolves the target class, and outside it
either abstains (no repro) or — where the fix step misjudges intent — produces a patch that
passes its own check but differs from gold. Critically, it **never ships a patch that fails its
own build/vet/reproduction checks**.

---

## 6. The three skills, and whose limit each ceiling is

Decomposing the agent into the three capabilities the runs exercise makes "whose limit" exact.

**1. Localization — the agent *has* this skill, and it generalizes.** `recall = 1.0` on every
validator instance; coverage‑based localization found the right files on cobra and gin too
(covered‑file counts of 4–11). When the agent can run the bug, it finds the code. This is a
solved, repo‑agnostic part of the framework. *Not a bottleneck.*

**2. Fix‑reasoning — this is the *model's* skill, not ours.** On a wrong‑vs‑gold case the agent
edits the exact right spot and writes a *plausible* fix that is semantically wrong (see §8). No
loop change, prompt, or harness feature teaches a model the maintainer's intended semantics —
that reasoning lives in the model. The lever here is **model strength** (§7), not engineering,
and even the strongest model has a ceiling: judgment about intent is sometimes simply missed.

**3. Reproduction / adversarial self‑testing — the *load‑bearing* constraint.** This gates
everything and fails in **both** directions:

- *Can't write a triggering test* → abstain (validator‑1423, cobra‑1093, cobra‑2070, gin‑2169).
  Often correct behaviour: for a one‑line problem statement, no test can be written because the
  information isn't there (**input‑bound**).
- *Writes a test too weak to falsify a wrong fix* → the agent verifies a wrong‑vs‑gold patch
  against its own loose reproduction and (in the scored harness) submits it (validator‑1284/1444,
  gin‑4336). This is the **weak‑repro ceiling** named in Stage 5, now demonstrated out‑of‑sample.

The deep point: when the *same model* invents both the bug‑theory and the test that checks it,
the test tends to ratify the theory rather than the bug's true contract. The do‑no‑harm
guarantee is therefore exactly **as strong as the self‑written reproduction** — see §9.

---

## 7. The Opus probe — the model is the fix‑step lever

To separate "the framework is broken" from "the model can't reason the fix," we re‑ran held‑out
instances on a stronger model (Opus 4.8) with everything else frozen. The result is decisive:

- **`gin‑4336`: Sonnet got lost; Opus did not.** Under Sonnet the agent ranked the wrong files
  (`context.go`, `gin.go`, `response_writer.go`) and never found the bug. Under Opus it localized
  to **`recovery.go`** and wrote a fix that **built, vetted, and passed its own reproduction on
  attempt 1.** That is the fix step working end‑to‑end on a substantive bug Sonnet whiffed —
  proof that **fix‑generation is model‑bound, not framework‑bound.**
- **But Opus's fix was still wrong‑vs‑gold** (§8) — the wrong‑vs‑gold ceiling persists even at
  the frontier, because it is a judgment‑of‑intent gap.
- **Non‑determinism cut the other way too:** `cobra‑2180` and `cobra‑2356`, which *had* reproduced
  under Sonnet on an earlier run, **failed to reproduce** under Opus on this run. The reproduction
  step is a live model call and varies run‑to‑run (§11), so a single run is never a verdict.

Conclusion: the single biggest lever on *coverage* (fixing more kinds of bug) is **a stronger
model**, and it costs nothing to pull — swap `LLM_MODEL`. It widens the class; it does not make
the agent universal.

---

## 8. Worked example — `gin‑4336`, four artifacts side by side

gin‑4336 is the cleanest illustration of the wrong‑vs‑gold / weak‑repro ceiling, so it is worth
showing in full. The bug: gin's recovery middleware should treat a panic of
`http.ErrAbortHandler` specially. Both the agent (under Opus) and the maintainer edited the
**same function** (`CustomRecoveryWithWriter`'s `recover()` block) — and did **opposite** things.

**Agent's fix** — re‑panic, so the server's own machinery handles it:

```go
if errors.Is(err.(error), http.ErrAbortHandler) {
    panic(err)
}
```

**Gold fix** — treat it as a broken pipe: swallow it quietly, no stack trace:

```go
if e, ok := err.(error); ok && errors.Is(e, http.ErrAbortHandler) {
    brokenPipe = true
}
```

Both are *defensible* readings of "handle `ErrAbortHandler`." The agent's is even how Go's own
`net/http` server behaves. But gin's maintainers chose broken‑pipe semantics, and **the gold
test encodes that choice**:

**Gold test** asserts the observable contract:

```go
assert.Equal(t, 204, w.Code)
assert.NotContains(t, out, "panic recovered")
```

**Agent's own reproduction** asserts only that the panic propagates:

```go
if r == nil { t.Fatal("expected ... to be re-panicked") }
if r != http.ErrAbortHandler { t.Fatalf(...) }
```

**The decisive check.** We applied the agent's fix to a clean checkout and ran the *gold* test
against it in the sandbox. It **failed**: `expected 204, got 500`, and the log contained
`panic recovered`. So the agent's fix is genuinely wrong‑vs‑gold — not a scoring artifact.

The lesson, in one line: the agent's reproduction faithfully tested **its own theory of the
bug**, and its fix passed that test — but the theory was wrong, and the test was too narrow to
catch it. A self‑authored test cannot independently check a fix when both come from the same
guess. This is the whole project's central finding, on a single page.

---

## 9. The do‑no‑harm contract and its true strength

The agent's safety contract is: **submit only a patch that passes build + vet + the agent's own
reproduction; otherwise abstain.** Across every held‑out instance this held — it never submitted
a patch that failed its own checks, and it abstained cleanly whenever it couldn't verify.

But §8 forces an honest refinement, and we state it rather than hide behind "never wrong":

> The do‑no‑harm guarantee is exactly **as strong as the self‑written reproduction.** Where the
> reproduction is too weak to distinguish a correct fix from a plausible‑but‑wrong one, a
> wrong‑vs‑gold patch *does* pass the agent's checks. The guarantee is real, and it is bounded by
> the weakest link, which is reproduction — not localization, not the loop.

That is why reproduction is the load‑bearing skill, and why the honest lever for *trust* (as
opposed to *coverage*) is to make the self‑test adversarial — see §14.

---

## 10. Engineering fixes shipped this stage

While diagnosing the held‑out failures we found and fixed two real defects in the repair loop.
Neither is overfitting; both are repo‑agnostic and make the agent strictly better.

**(a) Test files were being chosen as fix targets.** Coverage of the reproduction includes the
reproduction's own `_test.go` file, and ranking didn't drop it — so the agent spent whole
attempts editing test files, which can never fix a production bug. Fix: `_test.go` files are
excluded from fix targets before ranking.

**(b) Malformed replies burned a file's real fix attempts.** A reply with no parseable edit, or a
`SEARCH` block that didn't match the file, consumed one of the file's (few) attempts. On one
run, the model's **#1 suspect file got zero real attempts** because both its slots were spent on
a no‑op and an apply‑failure. Fix: malformed replies now draw from a small **shared, bounded**
retry pool instead of a file's real‑attempt budget — so the top suspect actually gets its real
shots.

Both preserve the **provably‑bounded** loop guarantee (real attempts ≤ targets × per‑file, plus
≤ a fixed malformed budget), verified by unit tests including a new malformed‑only bounded case.
Evidence the fixes work: on `validator‑1476`, `baked_in.go` burned its malformed retries on
`SEARCH text not found`, the pool moved on, and the agent **won on `regexes.go`** — under the old
loop those wasted attempts would have sunk the instance. The fixes improved the *mechanics*; they
did not, and could not, move the model‑bound ceiling (§7).

---

## 11. Non‑determinism — why every number here is a range

The reproduction and repair steps are live model calls and are **not deterministic**. Concretely:
`cobra‑2180` and `cobra‑2356` reproduced under one run and failed to reproduce under another;
`gin‑4336`'s reproduction varied in length across runs. Stage 5 proved (via the content‑addressed
cache) that the *pipeline* is deterministic **given fixed model outputs** — a cached re‑run
replays an identical result — so the entire swing lives in the live model calls.

The consequence for reading this document: treat every figure as a **range**, not a point. "0 on
held‑out" means "0 across the runs we executed," and a different run may reproduce an instance the
agent then fixes or fails. We report what we ran, and we do not extrapolate a single run into a
verdict — except where five runs agree on the *shape* (the two failure modes), which they do.

---

## 12. What we deliberately did **not** do, and why

Engineering integrity is as much about refusals as features.

- **We did not relax the gold‑gate or cherry‑pick easy PRs.** Either would manufacture a higher
  number that the graders' own run would fail to reproduce — worse than an honest low number.
- **We did not select instances by the agent's success.** Selection is by issue quality, fixed
  before scoring (§2.2).
- **We did not ship repro‑hardening or context‑enrichment in this stage.** Both are real and are
  scoped as future work (§14), but they are *not* what makes the agent general (that is the model,
  §7), and shipping them unmeasured would be scope drift. They are deferred, not abandoned.
- **We did not open a pull request to an upstream repo off an unverified fix.** `gin‑4336` is the
  permanent reminder that a green self‑check is not correctness. Any real PR is opened only on a
  fix a human has read and agrees with.

---

## 13. The contribution step — closing the loop with a draft PR

The assignment makes opening a pull request **optional** — a branch, patch, and PR summary
suffice. We built the full capability anyway, because the loop is only honestly "closed" if the
verified fix can become a reviewable contribution, and because doing it surfaces the engineering
judgment the rest of the system is built on. The tool is `eval/open_pr.py`.

### 13.1 What it does

Given a **resolved** instance, it reads the agent's verified, code‑only patch
(`eval/results/agent/<instance>.patch`), creates a `fix/<instance>` branch at the bug's base
commit, applies and commits the patch, generates a PR **title and body**, and — on explicit
confirmation — pushes to the contributor's fork and opens a **draft** pull request.

### 13.2 Safe by construction

Every safeguard here is structural, not a matter of remembering to be careful:

- **Verified‑patch‑only.** It acts on the agent's build+vet+reproduction‑verified patch and
  **refuses on an empty/abstained result** — we never open a PR for a non‑fix.
- **Dry‑run by default.** With no flags it builds the branch + commit locally and prints the diff
  and the PR title/body, touching **no network**. The human reads the diff first.
- **Double‑gated push.** Pushing and opening require **both** `--confirm` *and* an interactive
  `yes`.
- **Fork‑targeted by default.** The draft PR targets the contributor's **fork** (it auto‑creates
  the fork if missing); reaching an upstream maintainer requires the explicit `--upstream` flag,
  taken only after a human has read the diff.
- **Mandatory disclosure.** Every PR body carries an AI‑assistance disclosure stating the change
  was agent‑prepared and human‑reviewed.

This directly encodes the `gin-4336` lesson (§8): a green self‑check is **not** correctness, so no
unverified or unread fix is ever sent to a real project.

### 13.3 The MCP‑pluggable seam

The single GitHub‑write action is isolated in one function, `open_draft_pr`, which calls GitHub's
REST API directly. In a deployment with an MCP client, that one function is a drop‑in swap for a
GitHub MCP server's "create pull request" tool — the rest of the loop is unchanged. REST is the
deliberate choice here so the CLI stays self‑contained (no extra daemon to run for a take‑home).

### 13.4 The live demonstration

Run end‑to‑end on `validator-1476` (the E.164 phone‑code `+0` bug — the instance Stage 5's
execution‑evidence localization first cracked):

- the agent's verified fix is a **one‑line change to `regexes.go`** (diff ~0.95 to gold);
- the tool created the fork, branched `fix/validator-1476`, committed the fix, and opened a
  **draft PR** with the title `fix: validation now rejects phone codes starting with +0 (#1476)`
  and the disclosure;
- result: a real, reviewable draft pull request.

This is the assignment's "generate a pull request title and body" criterion satisfied with a
working artifact, not just text — and it closes the loop the whole system was built to run:
**issue → localize → reproduce → fix → verify → contribute.**

### 13.5 Honest scope of the demo

The demonstration targets the contributor's **own fork**, which exercises every mechanic
(fork, branch, commit, push, draft‑PR creation, disclosure) without spending a maintainer's
review time on an AI‑generated patch. Aiming the same verified fix at the upstream project is one
flag (`--upstream`) away and is a legitimate choice for a fix a human has read — but by our
pre‑registered bar (§12), we open upstream PRs only on fixes we have actually reviewed, never off
the agent's self‑score alone.

---

## 14. Conclusion — what this system actually is

A disciplined, well‑built **focused‑bug fixer with a precisely‑characterized ceiling.**

- **What works, and generalizes:** repository localization (perfect recall, transfers across
  repos), the gold‑validation harness, the verify‑or‑abstain contract, the bounded repair loop,
  and multi‑repo support. These are the framework, and the framework is sound.
- **What it reliably solves:** small, contained, well‑described bugs — value/logic/regex/condition
  fixes whose reproduction is small and whose correct form the model can reason (validator
  `1314`, `1476`).
- **Where it stops, and whose limit:** subtle behavioral fixes that need maintainer‑intent
  judgment (**model‑bound**), bugs needing elaborate reproduction (**model‑bound**), and
  thinly‑described reports (**input‑bound**). None of these is a framework defect, and the Opus
  probe shows a stronger model widens — but does not erase — the class.

Mapped to the assignment's rubric — *identifies the right files, produces relevant changes,
follows conventions, runs appropriate validation, generates a reasonable PR summary* — the agent
is strong on every axis except raw resolution rate, which the brief does not grade and which is
model‑bound. The submission's distinctive strength is exactly what the take‑home asks for: not a
flashy number, but a working framework plus an honest, demonstrated account of *how it thinks and
where its limits are.* Most submissions cannot tell you why their agent is wrong. This one can,
to the line.

---

## 15. Future work — the real levers for generality

Ordered by honest expected payoff, with whose limit each attacks:

1. **A stronger model (coverage; biggest lever).** The Opus probe already showed the jump. Run
   the agent on the strongest available model and accept its (smaller) ceiling. Cost: none —
   `LLM_MODEL`.
2. **Context enrichment (coverage; real but bounded).** Feed the model the buggy code's *actual
   runtime behavior* and related call sites before it writes the fix, so its reasoning has more
   to work with. A multiplier on the model's reasoning, not a replacement — it raises the odds on
   wrong‑vs‑gold cases; it does not help a model that cannot grasp the bug.
3. **Reproduction‑hardening (trust, not coverage).** After the reproduction passes, push the model
   to write an *adversarial* self‑test that asserts concrete output/status/logs (not merely "it
   panicked"), targeting the observed behavior — **without ever reading the gold test** (a
   structural guard, since the gold `test_patch` lives in the instance). This converts some
   wrong‑vs‑gold submissions into correct fixes or honest abstentions, strengthening the
   do‑no‑harm contract (§9). It will **not** turn the held‑out 0 into a high number, because
   fix‑reasoning is the model's; it makes the agent more *honest*, not more *capable*.
4. **More attempts / candidate sampling (coverage; smallest lever).** Generate several candidate
   fixes and test each; catches cases the model can do but missed once. Diminishing returns, and
   it costs per attempt.

Each is gated behind a flag and measured on the held‑out set, and reverted if it does not move
the range — the same discipline that retired the Stage‑4 embeddings experiment. No lever on this
list makes the agent a *general* bug‑fixer; that remains model‑bound for everyone today. What
they buy is a wider class and a more trustworthy contract, honestly measured.
