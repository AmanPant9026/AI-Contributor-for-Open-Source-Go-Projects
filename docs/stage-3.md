# Stage 3 — The Agent's Tools (Eyes and Hands): The Complete, Detailed Reference

> **Status:** complete; locked at git tag `gate-3` (21 unit tests green, plus a
> real-validator + live-Docker smoke).
> **One-line summary:** a set of seven small, **deterministic** tools that let the
> (future) agent *see* a Go repository — read files, search, navigate symbols,
> and get a ranked map of the codebase — and *act on* it — apply a patch and run
> the Go toolchain in the sandbox — each one unit-tested in isolation so a tool
> bug can never later masquerade as a model mistake.

This is the long-form reference, written to the same what/why/how depth as
`stage-1.md` and `stage-2.md`. It explains every concept from first principles,
dissects every file and function, and works through concrete examples taken from
the real `go-playground/validator` codebase. If you read it top to bottom you
should understand not just *what* each tool is, but *why it exists* and *how it
actually runs*.

---

## Table of contents

1. [Why this stage exists (and what it deliberately is NOT)](#1-why)
2. [Core vocabulary, explained properly](#2-vocab)
3. [The file layout — everything Stage 3 added](#3-files)
4. [The seven tools at a glance](#4-overview)
5. [`fileio` — safe basic file access](#5-fileio)
6. [`read_span` — read just the lines you need](#6-readspan)
7. [`search_code` — ripgrep with a Python fallback](#7-search)
8. [`apply_patch` — tolerant, non-blocking diff application](#8-apply)
9. [`go_tools` — the Go toolchain in the sandbox](#9-gotools)
10. [`ast_nav` — tree-sitter symbol extraction](#10-astnav)
11. [`repo_map` — the ranked, budgeted map (the hard one)](#11-repomap)
12. [PageRank, explained from scratch](#12-pagerank)
13. [The fixture and the gate (`gate-3`)](#13-gate)
14. [The smoke script — verifying on real validator](#14-smoke)
15. [Real-repo hardening: bugs the fixture couldn't catch](#15-hardening)
16. [Dependencies, environment, and gotchas](#16-env)
17. [Inspect and re-run it yourself](#17-inspect)
18. [What Stage 3 unlocks](#18-unlocks)

---

<a name="1-why"></a>
## 1. Why this stage exists (and what it deliberately is NOT)

By the end of Stage 2 we had a graded answer key (Stage 1) and a trustworthy
scorer (Stage 2). What we still had **nothing** for was the act of *looking at* a
repository or *changing* it. An agent that fixes bugs needs, at minimum, to be
able to: open files, search for things, understand where code lives, edit code,
and check whether the edit compiles and passes tests. Stage 3 builds exactly that
toolbox.

**The single most important property of every Stage 3 tool is that it is
deterministic.** Given the same input, each tool returns the same output, every
time, with no model and no randomness involved. That is what lets us unit-test
each one against a tiny fixture and *know* it works. It directly serves a core
project principle: build the deterministic scaffolding first and add the LLM last,
so that when something goes wrong later we can tell a *tool* bug apart from a
*model* mistake.

**What Stage 3 is NOT:**

| not in Stage 3 | why / where it lives |
|---|---|
| any LLM call, prompt, or model | Stage 4 — the tools are the agent's hands, not its brain |
| a "decide what to do next" loop | Stage 4 — the localize→repair→validate spine |
| wiring the tools together into a workflow | Stage 4 — here each tool is an independent, separately-tested unit |
| changes to the Stage-1 answer key or the Stage-2 scorer | untouched; Stage 3 only *adds* the `tools/` and `indexing/` packages |

Think of Stage 3 as building and bench-testing each instrument on the agent's
control panel. Wiring the panel to a pilot (the LLM) is the next stage.

---

<a name="2-vocab"></a>
## 2. Core vocabulary, explained properly

| term | one-line meaning |
|---|---|
| Tool | a small, deterministic function the agent can call to see or change the repo |
| AST (abstract syntax tree) | a tree representation of source code's structure, produced by a parser |
| tree-sitter | a fast, accurate parser library; we use its Go grammar to read `.go` files |
| Symbol | a defined thing in a Go file: a function, method, or type |
| Signature | a symbol's declaration line **without** its body (high signal, low cost) |
| Span | a contiguous range of lines in a file (e.g. lines 1403–1427) |
| ripgrep (`rg`) | a very fast command-line search tool; respects `.gitignore` |
| Unified diff / patch | the precise add/remove recipe between two versions of files (see stage-1 §5) |
| Reference graph | a network of files, with an edge A→B when A uses a symbol defined in B |
| PageRank | an algorithm that scores a node "important" if important nodes point to it |
| Token budget | an approximate cap on output size (≈ chars ÷ 4) so a map fits the model's context |
| Go-ignored dir | a directory the Go toolchain skips for builds: names starting with `_` or `.`, and `testdata` |
| Sandbox | the pinned Docker container (from Stage 0) where Go build/test/vet run in isolation |
| Deterministic | same input ⇒ same output, every time (no model, no randomness) |

**Why "signatures, not bodies" recurs.** The agent's context window is small and
expensive. A function *signature* (`func isURL(fl FieldLevel) bool`) tells the
agent what exists and where, in a dozen tokens; the *body* might be hundreds of
tokens of detail it doesn't need yet. Several tools here trade bodies for
signatures on purpose, and fetch a body only on demand (via `read_span`).

---

<a name="3-files"></a>
## 3. The file layout — everything Stage 3 added

Two new packages under `src/go_issue_agent/` (`tools/` for I/O + action,
`indexing/` for code intelligence), plus a fixture, tests, and two scripts.

| file | kind | role |
|---|---|---|
| `src/go_issue_agent/tools/__init__.py` | package | re-exports the five action/IO tools |
| `src/go_issue_agent/tools/fileio.py` | tool | list / read / write files, sandboxed to a root |
| `src/go_issue_agent/tools/read_span.py` | tool | read a line range of a file (numbered) |
| `src/go_issue_agent/tools/search_code.py` | tool | regex search via ripgrep, with a pure-Python fallback |
| `src/go_issue_agent/tools/apply_patch.py` | tool | apply a unified diff tolerantly and non-interactively |
| `src/go_issue_agent/tools/go_tools.py` | tool | run `go build`/`vet`/`test`/`gofmt` in the sandbox |
| `src/go_issue_agent/indexing/__init__.py` | package | re-exports `ast_nav` and `repo_map` |
| `src/go_issue_agent/indexing/ast_nav.py` | tool | tree-sitter symbol extraction + definition lookup |
| `src/go_issue_agent/indexing/repo_map.py` | tool | ranked, token-budgeted repo "table of contents" |
| `tests/test_tools.py` | test | the gate-3 unit tests (21) |
| `tests/fixtures/minirepo/` | fixture | a tiny Go module with known symbols/refs (see §13) |
| `scripts/gate3.sh` | gate | runs the unit tests; green = gate-3 |
| `scripts/stage3_smoke.py` | check | exercises every tool against the real validator clone (see §14) |
| `requirements.txt`, `pyproject.toml` | deps | add `tree-sitter` + `tree-sitter-go` |

**Reused (not modified) from earlier stages:**

| file | what Stage 3 uses it for |
|---|---|
| `src/go_issue_agent/sandbox/runner.py` | `run_in_sandbox(...)` (with the `extra_mounts` arg) — `go_tools` runs here |
| `src/go_issue_agent/models.py` | `CommandResult` (the `.ok` / `.tail()` return type for `go_tools`) |
| `src/go_issue_agent/config.py` | `settings.sandbox_image` — which Docker image to run |

---

<a name="4-overview"></a>
## 4. The seven tools at a glance

Grouped into three tiers, simplest first.

| tier | tool | one-line purpose | built on | needs Docker? |
|---|---|---|---|---|
| **A: I/O + search** | `fileio` | list/read/write files | plain Python | no |
| | `read_span` | read lines N..M | plain Python | no |
| | `search_code` | find a regex across the repo | ripgrep (+ fallback) | no |
| **B: code intelligence** | `ast_nav` | symbols (func/method/type) + definitions | tree-sitter | no |
| | `repo_map` | ranked, budgeted code map | tree-sitter + graph + PageRank | no |
| **C: validation/action** | `apply_patch` | apply a unified diff | git apply / `patch` | no |
| | `go_tools` | build / vet / test / fmt | sandbox runner (Docker) | **yes** |

Only `go_tools` touches Docker; everything else is pure host-side Python, which is
why six of the seven are trivially fast to test and the seventh is tested by
splitting its *command construction* (pure) from its *execution* (Docker).

---

<a name="5-fileio"></a>
## 5. `fileio` — safe basic file access

**What.** Three functions plus a safety helper.

| function | signature | returns | what it does |
|---|---|---|---|
| `list_files` | `(root, *, suffix=".go", include_tests=True)` | `list[str]` | repo-relative paths under `root`, sorted, optionally filtered by suffix; skips hidden (`.`-prefixed) path parts |
| `read_file` | `(root, rel)` | `str` | full file contents (UTF-8, errors replaced) |
| `write_file` | `(root, rel, content)` | `None` | write a file, creating parent dirs |
| `_safe` | `(root, rel)` | `Path` | resolves `rel` under `root` and **raises `ValueError` if it escapes** |

**Why.** The agent needs basic file access, but it must not be able to wander
outside the repository (e.g. read `/etc/passwd` via `../../`). `_safe` resolves
every path and confirms it stays inside `root`, so traversal is blocked at the
tool boundary. `list_files` is sorted for determinism (so the agent and the tests
see a stable order), and `include_tests=False` lets callers exclude `_test.go`
files when they only care about library code.

**How.** `_safe` calls `Path.resolve()` on both `root` and the joined target and
checks `root == target or root in target.parents`. `list_files` walks `rglob("*")`,
skips any path with a `.`-prefixed component, applies the suffix filter, and
returns relative paths.

**Worked example (real validator):**

```
list_files(validator, suffix=".go", include_tests=False)  -> 56 files
read_file(validator, "go.mod")                            -> "module github.com/go-playground/validator/v10\n…"
read_file(tmp, "../../etc/passwd")                        -> raises ValueError (blocked)
```

---

<a name="6-readspan"></a>
## 6. `read_span` — read just the lines you need

**What.** One function: `read_span(root, rel, start, end, *, with_line_numbers=True) -> str`.
Returns lines `[start, end]` (1-indexed, inclusive), each optionally prefixed with
its line number.

**Why.** Feeding a 3,000-line file to the model wastes its limited context. Once
`ast_nav`/`search_code` tell the agent *where* something is, `read_span` lets it
read just that function. Line numbers are included by default so the model can
reason about exact locations and produce precise edits.

**How.** Reads the file, splits into lines, **clamps** `start`/`end` to the real
bounds (so out-of-range requests never crash — they just return what exists), and
joins. If `start > end` after clamping it returns `""`. Paths go through `fileio._safe`,
so the same traversal protection applies.

**Worked example (real validator):**

```
read_span(validator, "baked_in.go", 1403, 1411)
1403   func isPostcodeByIso3166Alpha2Field(fl FieldLevel) bool {
1404           field := fl.Field()
1405           params := parseOneOfParam2(fl.Param())
…
read_span(validator, "go.mod", 5, 9999, with_line_numbers=False)   # over-range -> clamped, no crash
```

---

<a name="7-search"></a>
## 7. `search_code` — ripgrep with a Python fallback

**What.** `search_code(root, pattern, *, suffix=".go", max_results=200) -> list[Hit]`,
where `Hit` is `(path, line, text)`. Returns up to `max_results` matches, sorted by
`(path, line)`.

**Why.** Finding where a symbol or string appears is the bread-and-butter of
localization. ripgrep is extremely fast and respects `.gitignore`, but it may not
be installed everywhere — so the tool **falls back to pure Python** when `rg` is
absent, and both paths return identical results. That makes the tool (and its
tests) portable: it works on a machine with `rg` and one without.

**How.**

| step | detail |
|---|---|
| try ripgrep | `shutil.which("rg")`; if present, run `rg --line-number --no-heading --color never --glob '*<suffix>' <pattern> .` in `root` |
| read rg's exit code | `0` = matches, `1` = no matches (both fine), `>1` = real error → return `None` so the caller falls back |
| parse rg output | split each line on `:` into `path:line:content` |
| fallback (no rg) | walk `*<suffix>` files, skip `.`-prefixed dirs, `re.search` each line |
| finalize | sort by `(path, line)`, truncate to `max_results` |

**Worked example (real validator):**

```
search_code(validator, r"postcodeRegexInit")     # backend: ripgrep (or python-fallback if rg absent)
  baked_in.go:1392: postcodeRegexInit.Do(initPostcodes)
  baked_in.go:1420: postcodeRegexInit.Do(initPostcodes)
  postcode_regexes.go:171: postcodeRegexInit sync.Once
```

In end-to-end testing the rg path and the Python fallback returned **the exact
same locations**, which is the property we rely on.

---

<a name="8-apply"></a>
## 8. `apply_patch` — tolerant, non-blocking diff application

**What.** `apply_patch(repo_dir, diff_text) -> ApplyResult`, where `ApplyResult`
is `(applied: bool, empty: bool, method: str, detail: str)`.

| `method` | meaning |
|---|---|
| `"git"` | applied via `git apply` |
| `"patch"` | applied via the classic `patch` fallback |
| `"none"` | the diff was empty (a legitimate no-op) |
| `"failed"` | non-empty diff that would not apply (with stderr in `detail`) |

**Why.** The agent's whole output is a code change, so applying a diff reliably is
essential. Real diffs sometimes have slight line drift, so we apply *tolerantly*
(the same belt-and-suspenders approach proven in `verify_gt.sh` / `run_eval.py`):
`git apply` first, then fall back to `patch --fuzz=3`. Two failure modes must be
handled cleanly: an **empty** diff (treated as a successful no-op, used by the
"empty" candidate in eval) and a **malformed** diff (clean failure, not a crash).

**How — and the critical non-blocking fix.**

| step | detail |
|---|---|
| empty check | if `diff_text.strip()` is empty → `ApplyResult(applied=False, empty=True, method="none")` |
| write temp | write the diff to a temp `.patch` file (ensuring a trailing newline) |
| try git | `git -C repo apply --recount --ignore-whitespace <file>` → success ⇒ `method="git"` |
| fallback patch | `patch -d repo -p1 --fuzz=3 -N -r /dev/null -i <file>` with **`stdin=DEVNULL`** |
| classify | success ⇒ `method="patch"`; else `method="failed"` with combined stderr |
| cleanup | delete the temp file |

The flags on the `patch` fallback exist because of a real bug found in end-to-end
testing (see §15): when re-applying an already-applied patch, `patch` prints
`Reversed (or previously applied) patch detected! Assume -R? [n]` and **waits for
keyboard input** — hanging the whole process on an interactive terminal. The fix:

- **`stdin=subprocess.DEVNULL`** — `patch` can never read from the keyboard, so it
  can never block; it gets EOF and proceeds with defaults.
- **`-N`** (forward) — patches that look reversed/already-applied are skipped
  cleanly (return failure) instead of prompting.
- **`-r /dev/null`** — reject files are discarded, so a failed apply never litters
  the checkout with `.rej`/`.orig` files.

**Worked example (real validator, instance 1314):**

```
apply_patch(validator@base_1314, gold_fix.patch)  -> applied=True  method=git   (init-call count 1 -> 2)
apply_patch(validator, same gold_fix.patch again) -> applied=False method=failed (clean, ~0.01s, no .rej)
apply_patch(validator, "total nonsense")          -> applied=False method=failed
apply_patch(validator, "   ")                      -> applied=False empty=True   method=none
```

---

<a name="9-gotools"></a>
## 9. `go_tools` — the Go toolchain in the sandbox

**What.** Pure command-builders plus sandboxed runners.

| builder (pure) | returns |
|---|---|
| `build_cmd(pkgs="./...")` | `"go build ./..."` |
| `vet_cmd(pkgs="./...")` | `"go vet ./..."` |
| `test_cmd(names=None, pkgs="./...")` | `"go test ./..."` or `"go test -run '^(A|B)$' ./..."` |
| `gofmt_check_cmd(go_files)` | `"true"` (empty) or `test -z "$(gofmt -l 'a.go' …)"` |

| runner (Docker) | runs |
|---|---|
| `go_build(repo_dir, pkgs="./...")` | `build_cmd` in the sandbox |
| `go_vet(repo_dir, pkgs="./...")` | `vet_cmd` |
| `go_test(repo_dir, names=None, pkgs="./...")` | `test_cmd` |
| `gofmt_check(repo_dir, go_files)` | `gofmt_check_cmd`, scoped to the given files |

Each runner returns a `CommandResult` (`.ok`, `.exit_code`, `.tail()`).

**Why.** The agent must compile, vet, format-check, and test its changes — but
**nothing should run Go on the host**; it runs inside the Stage-0 Docker sandbox so
model-generated code is contained and the toolchain is pinned/reproducible. The
build/runner split is deliberate so the *command strings* (the part that's easy to
get subtly wrong) can be unit-tested without Docker, while the actual execution is
exercised by the live smoke (§14).

**How.** The runners call `run_in_sandbox(repo_dir, cmd, image=settings.sandbox_image,
extra_mounts=[(".cache/gomod", "/go/pkg/mod")])`. The mount is the persistent Go
module cache from Stage 2 — dependencies download once per machine, not once per
run, which is what keeps repeated runs fast. `gofmt_check_cmd` is scoped to
specific files (never the whole tree) — the same fix Stage 2 made to its fmt gate
(stage-2 §4.3), so it judges only the candidate's files.

**Worked example (real validator, live Docker):**

```
go_build(validator, ".")   ->  exit=0  ok=True  (≈4.5s)   # the library compiles in the sandbox
test_cmd(["TestUrl"])      ->  "go test -run '^(TestUrl)$' ./..."
```

---

<a name="10-astnav"></a>
## 10. `ast_nav` — tree-sitter symbol extraction

**What.** Tree-sitter-backed structural reading of Go source.

| function | signature | returns | role |
|---|---|---|---|
| `parse_source` | `(src: bytes)` | `list[Symbol]` | top-level symbols from source bytes |
| `parse_file` | `(path)` | `list[Symbol]` | same, from a file on disk |
| `identifiers` | `(src: bytes)` | `list[str]` | every identifier token used (with duplicates) — feeds `repo_map`'s graph |
| `find_definitions` | `(root, name)` | `list[(rel_path, Symbol)]` | where `name` is defined across the repo |

A `Symbol` is `(kind, name, start_line, end_line, signature)` where `kind` ∈
{`func`, `method`, `type`}, lines are 1-indexed inclusive, and `signature` is the
one-line declaration (no body).

**Why.** Plain text search can't tell you "the functions and types defined in this
file, and their exact line ranges." That structural view is what lets the agent
(and `repo_map`) reason about code by symbol rather than by raw bytes. We keep
signatures, not bodies, for the context-cost reason in §2.

**How.**

| step | detail |
|---|---|
| parse | tree-sitter parses the bytes into a syntax tree; we look at the root's direct children |
| pick defs | keep `function_declaration`, `method_declaration`, `type_declaration` nodes |
| names | read each node's `name` field; for grouped `type ( A …; B … )` we iterate the inner `type_spec`s |
| signature | take text from the node start up to the `body` block (or the first `{` for types), collapsed to one line |
| `identifiers` | walk the whole tree, collect `identifier` / `type_identifier` / `field_identifier` tokens |
| `find_definitions` | scan all `.go` files (skipping `_test.go` and Go-ignored dirs), return matches |

**Worked example (real validator `baked_in.go`):**

```
parse_file("baked_in.go")  -> 178–193 symbols (varies with the checked-out commit)
  [func] L1403-1427: func isPostcodeByIso3166Alpha2Field(fl FieldLevel) bool
  [type] L37-37:     type Func func(fl FieldLevel) bool
find_definitions(validator, "isURL")  -> [("baked_in.go", Symbol(func, "isURL", …))]
```

In end-to-end testing, `ast_nav` parsed **all 81** real validator `.go` files with
**zero crashes** — real-world Go (generics, grouped types, interfaces, methods
with pointer receivers) all parse cleanly.

> Why the symbol count "drifts" (178 vs 184 vs 193): the number tracks **whichever
> commit the clone is checked out at**. After a `gate-gt` run the clone is left at
> some bug's `base_commit`, an older snapshot of `baked_in.go` with fewer
> functions. This is expected; the *determinism* check (same input ⇒ same output)
> is the real stability signal, not the absolute count.

---

<a name="11-repomap"></a>
## 11. `repo_map` — the ranked, budgeted map (the hard one)

**What.** `build_repo_map(root, *, budget_tokens=3000, personalization=None) -> RepoMap`,
where `RepoMap` is `(ranked_files: list[FileInfo], skeleton: str, truncated: bool)`.
The `skeleton` is a compact, human-readable "table of contents": for each file (most
important first), its symbol signatures with line numbers, cut off at the token budget.

**Why.** Imagine being handed a 200-file codebase and asked "where would you fix a
postcode bug?" You wouldn't read everything — you'd want a table of contents with
the *important* files near the top. That's what `repo_map` produces. It is the
single most leveraged tool for the **localization** grading axis: it lets the agent
(later) orient cheaply, without burning its context reading every file. This is the
one genuinely sophisticated tool, and you chose the full version (tree-sitter +
reference graph + PageRank).

**How — the four steps:**

| step | function | what happens |
|---|---|---|
| 1. PARSE | `_collect` | parse every non-test, non-ignored `.go` file → symbols; build `name → defining_file` |
| 2. GRAPH | `_build_graph` | for each file, for each identifier it uses that is defined in *another* file, add a weighted edge referrer → definer |
| 3. RANK | `pagerank` | run PageRank over that graph; a file is important if important files reference its symbols (§12) |
| 4. SKELETON | `build_repo_map` | order files by rank (tie-break by path for determinism); emit signatures until the token budget is hit |

**The Go-ignored-dir rule (`_build_ignored`).** `_collect` skips any file whose
path has a component starting with `_` or `.`, or named `testdata` — exactly the
directories the Go toolchain itself ignores for builds. This was added after the
real-repo test showed validator's `_examples/` files polluting the ranking (§15).

**The token budget (`_est_tokens`).** Tokens are estimated as `len(text) // 4`
(≈4 chars/token). The skeleton appends file blocks until adding the next would
exceed `budget_tokens`, then sets `truncated=True`. Good enough to keep the map
within a model's context window without a real tokenizer dependency.

**Worked example (real validator, `budget_tokens=1500`):**

```
RANKING (most important first):
  0.3701  errors.go              (20 symbols)
  0.1825  validator_instance.go  (31 symbols)
  0.1724  translations.go        (2 symbols)
  0.0468  cache.go               (13 symbols)
  0.0351  validator.go           (4 symbols)
  0.0221  baked_in.go            (178 symbols)
  …
_examples files present? -> NONE (correct)
deterministic across two builds? -> True

SKELETON (first lines):
errors.go
  type ValidationErrorsTranslations map[string]string  [L17]
  type InvalidValidationError struct  [L21]
  func (e *InvalidValidationError) Error() string  [L26]
  …
```

`errors.go` ranks #1 because its types (`FieldError`, `ValidationErrors`) are
referenced all over the library — exactly the kind of central file you'd want at
the top of a map.

**The `personalization` hook.** `build_repo_map` accepts an optional
`personalization` dict that biases PageRank's "random jump" toward chosen files
(§12). It defaults to `None` (plain global ranking) in Stage 3; it exists so Stage 4
can *focus* the map on files whose names/symbols match the issue text, making the
map issue-aware. We built the hook now and leave it unused until then.

---

<a name="12-pagerank"></a>
## 12. PageRank, explained from scratch

**The intuition.** PageRank is the algorithm Google originally used to rank web
pages. The idea: a page is "important" if many important pages link to it.
Importance flows along links and settles into a stable score. We apply the same
idea to files: a file is important if many important files **reference the symbols
it defines**. So our edges point **referrer → definer**, and the heavily-depended-on
files (like `errors.go`) accumulate high scores.

**Our implementation (`pagerank`, simple power iteration):**

| concept | what it is | in our code |
|---|---|---|
| nodes | the files | keys of `graph` |
| edge `a→b` weight `w` | file `a` uses symbols of `b`, `w` times | `graph[a][b] = w` |
| damping `d` (0.85) | chance importance flows along edges (vs a random jump) | `damping=0.85` |
| teleport | where a "random jump" lands (uniform, or `personalization`) | `teleport[x]` |
| dangling node | a file with no out-edges (references nothing) | `out_w[x] == 0` |
| iteration | repeatedly redistribute scores until they stop changing | `for _ in range(iterations)` |
| convergence | stop when the total change `< tol` | `if delta < tol: break` |

Each iteration, every node's new score = a base teleport share `(1−d)·teleport[x]`,
plus a share of the "dangling mass" (scores of nodes that point nowhere,
redistributed so nothing leaks), plus `d ×` the weighted scores flowing in along its
in-edges. Scores always sum to 1. With `personalization`, the teleport distribution
is skewed toward chosen files, so the whole ranking tilts toward them — the Stage-4
focusing hook.

**Worked example (the fixture):** `core.go` defines `Hello` and `Config`, which
`a.go`, `b.go`, and `main.go` all use. PageRank gives:

```
0.4928  core.go     <- referenced by everyone -> highest
0.1825  a.go
0.1825  b.go
0.1422  main.go
```

This is exactly the deterministic assertion the gate checks: the central file
ranks first.

---

<a name="13-gate"></a>
## 13. The fixture and the gate (`gate-3`)

**The fixture (`tests/fixtures/minirepo/`).** A tiny, hand-built Go module whose
reference structure we control, so every tool's output is *known* and assertable.

| file | purpose |
|---|---|
| `go.mod` | makes it a module |
| `core.go` | defines `Hello` (func) and `Config` (type) — the central symbols |
| `a.go` | `A()` calls `Hello` |
| `b.go` | `B(c Config)` uses `Config` and `Hello` |
| `main.go` | `Run()` calls `A` and `B` |
| `core_test.go` | a `_test.go` file — proves tools skip tests where they should |
| `_examples/demo.go` | under a Go-ignored dir — proves `repo_map`/`find_definitions` exclude it |

**The gate (`scripts/gate3.sh`).** Runs `pytest tests/test_tools.py`. Green = gate-3.
The 21 tests, by tool:

| tool | what the tests assert |
|---|---|
| `fileio` | go-only + sorted listing; can exclude tests; read/write round-trip; **path escape raises** |
| `read_span` | inclusive numbered range; clamps out-of-range |
| `search_code` | finds the symbol in the right files at real line numbers |
| `ast_nav` | extracts func + type with right kind/signature/line; `find_definitions` skips tests **and ignored dirs** |
| `repo_map` | **ranks the central file first**; skeleton has signatures not bodies; budget truncates; excludes `_examples`; PageRank sums to 1 |
| `apply_patch` | applies a real diff; empty is a no-op; **re-apply fails cleanly (no hang)**; garbage fails |
| `go_tools` | command builders produce exact strings; runner calls the sandbox with the module-cache mount (monkeypatched, no Docker) |

The `go_tools` execution is monkeypatched (no Docker needed) so the gate is fast
and runs anywhere; the *real* Docker execution is covered by the smoke (§14).

---

<a name="14-smoke"></a>
## 14. The smoke script — verifying on real validator

**What.** `scripts/stage3_smoke.py` runs every tool against your *actual*
`.cache/repos/validator` clone and prints human-readable output, with a "GOOD if…"
line under each section so you can confirm with your own eyes.

| section | tool | what to look for |
|---|---|---|
| 1 | `repo_map` | library files (`errors.go`, `validator*.go`) on top; no `_examples`; deterministic = True |
| 2 | `ast_nav` | the big `baked_in.go` parses to ~180+ symbols; postcode functions show line ranges |
| 3 | `search_code` | hits in `baked_in.go` + `postcode_regexes.go`; reports `ripgrep` or `python-fallback` |
| 4 | `read_span` | the function header + first lines, numbered |
| 5 | `apply_patch` | real gold patch applies (`1 → 2`); re-apply False; garbage failed; empty noop |
| 6 | `go_tools` | **LIVE Docker `go build .` → `ok=True`** (the part unit tests stub) |

**Why a separate smoke.** Unit tests prove the *logic* on a fixture; the smoke
proves the tools work on *real, messy code* and a *real sandbox*. Section 6 is the
most important: it's the only place the genuine `docker run` executes, so `ok=True`
there is the proof that `go_tools` works for real, not just in a stubbed test.

**Confirmed end-to-end result on your Mac:** sections 1–5 all met their "GOOD if",
and section 6 reported `exit=0 ok=True (≈4.5s)` — the validator library compiled
inside the pinned sandbox. Stage 3 is working end-to-end.

---

<a name="15-hardening"></a>
## 15. Real-repo hardening: bugs the fixture couldn't catch

Two genuine bugs surfaced only when the tools met real validator, not the toy
fixture. Both are now fixed and locked by tests.

| bug | how it showed up | root cause | fix | locked by |
|---|---|---|---|---|
| `repo_map` ranked `_examples/` files near the top, eating the budget | top-8 ranking on real validator listed `_examples/struct-level/main.go` at #2 | `_collect` walked *every* `.go` file; Go-ignored dirs (`_`-prefixed) are not part of the library | skip paths with a `_`/`.` component or `testdata` (`_build_ignored`), in both `repo_map` and `find_definitions` | `test_repo_map_excludes_go_ignored_dirs`, `test_find_definitions_excludes_ignored_dirs` |
| `apply_patch` **hung** when re-applying an already-applied patch | the smoke froze right after section 5's first apply | the classic `patch` tool prompts `Assume -R? [n]` and waits on stdin; on a real terminal it blocked forever | `stdin=DEVNULL` + `-N` (skip reversed/applied) + `-r /dev/null` (discard rejects) | `test_reapply_already_applied_fails_cleanly` |

Why the hang slipped past the fixture gate: under pytest, stdin is captured (not a
terminal), so `patch` got EOF and failed fast — the gate passed. The interactive
smoke, run in a real terminal, gave `patch` a TTY to prompt on, so it hung. The
structural fix (`stdin=DEVNULL`) makes blocking impossible in *both* settings. This
is exactly the kind of issue the "test on real data, end-to-end" step is for.

---

<a name="16-env"></a>
## 16. Dependencies, environment, and gotchas

**New dependencies (added to `requirements.txt` and `pyproject.toml`):**

| dependency | role |
|---|---|
| `tree-sitter` (>=0.21) | the parser runtime |
| `tree-sitter-go` (>=0.21) | the Go grammar `ast_nav` loads |

**Optional:**

| tool | effect if present | effect if absent |
|---|---|---|
| `ripgrep` (`rg`) | `search_code` uses the fast rg path | `search_code` uses the pure-Python fallback (identical results) |
| Docker | `go_tools` and smoke section 6 run for real | they self-skip; the rest still works |

**Gotchas:**

| symptom | cause | resolution |
|---|---|---|
| smoke hangs after "init-call count 1 → 2" | old `apply_patch` blocked on a `patch` prompt | fixed (§15); take the current `apply_patch.py` |
| `_examples` files appear in the repo map | old `repo_map` didn't skip Go-ignored dirs | fixed (§15) |
| symbol count differs run to run (178/184/193) | the clone is at a different `base_commit` | expected; trust the determinism check, not the absolute number |
| `search_code` says `python-fallback` | `rg` not installed | fine (identical results); `brew install ripgrep` to use the fast path |
| `go_tools` "skipped — docker not found" | Docker not on PATH / not running | start Docker Desktop to run section 6 |

---

<a name="17-inspect"></a>
## 17. Inspect and re-run it yourself

| goal | command |
|---|---|
| run the gate (unit tests) | `bash scripts/gate3.sh` |
| run the real-repo smoke (incl. live Docker) | `python scripts/stage3_smoke.py` |
| see the repo map of validator | `python -c "import sys;sys.path.insert(0,'src');from go_issue_agent.indexing import repo_map as r;print(r.build_repo_map('.cache/repos/validator',budget_tokens=1500).skeleton)"` |
| list a file's symbols | `python -c "import sys;sys.path.insert(0,'src');from go_issue_agent.indexing import ast_nav as a;[print(s) for s in a.parse_file('.cache/repos/validator/baked_in.go')]"` |
| search the repo | `python -c "import sys;sys.path.insert(0,'src');from go_issue_agent.tools import search_code as s;[print(h) for h in s.search_code('.cache/repos/validator','isURL')]"` |

(Use the project's `python` — the activated env with `tree-sitter` installed.)

---

<a name="18-unlocks"></a>
## 18. What Stage 3 unlocks

- **A complete, tested toolbox for the agent.** Seven deterministic tools covering
  see (`fileio`, `read_span`, `search_code`, `ast_nav`, `repo_map`) and act
  (`apply_patch`, `go_tools`) — each proven in isolation, and all proven together
  against real validator and a real sandbox.
- **Localization leverage.** `repo_map` (PageRank-ranked, budgeted, issue-focusable
  via the personalization hook) gives the agent a cheap, high-signal way to find
  the right files — directly serving the localization grading axis.
- **A safe boundary.** Path traversal is blocked (`fileio`), patches apply without
  ever blocking (`apply_patch`), and all Go execution is contained in the sandbox
  (`go_tools`).
- **The green light for Stage 4 — the agent loop.** With eyes and hands proven, the
  next stage wires them to the LLM along the localize→repair→validate spine: use
  `repo_map`/`search_code`/`ast_nav` to localize, `read_span` to gather context,
  the model to propose a patch, `apply_patch` to apply it, and `go_tools` + the
  Stage-2 scorer to validate — looping until the bug test passes or a budget is hit.

---

*This document is meant to be exhaustive. If any row, function, or worked example
is unclear, name it and I'll expand that one spot. As always, no advancing to
Stage 4 without your go-ahead and a what/why/how plan first.*
