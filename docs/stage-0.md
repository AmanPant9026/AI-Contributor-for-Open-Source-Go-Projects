# Stage 0 — Bedrock

> **Status:** complete and locked at git tag `gate-0`.
> **One line:** we built a pinned, isolated environment that can run Go on a real repo checkout and *objectively tell a passing build from a failing one* — the trustworthy foundation everything else stands on.

---

## 1. The mental model

We are building **two** things, and the order is deliberate:

1. **The agent** — turns a GitHub issue into a code patch (built later).
2. **The ruler** — the environment + answer key that *measures* whether a patch is correct (built first).

Stage 0 is the **floor under the ruler**. Before any agent logic or any answer key exists, we need one thing we can trust absolutely: a place to run Go code that is identical every time and that reports success/failure honestly. Everything later — "did the agent fix the bug?" — ultimately reduces to "did the sandbox compile and test it, and did it pass?" If the sandbox itself can't be trusted to distinguish pass from fail, nothing above it means anything.

So the governing idea is **outside-in**: build the deterministic, testable scaffolding first; add the unpredictable LLM last. Stage 0 is the most deterministic layer of all.

```
        ┌─────────────────────────────────────────────┐
        │  YOUR MAC (host)                              │
        │                                               │
        │   repo.py ──clone/checkout──► .cache/repos/   │
        │      validator @ v10.24.0                     │
        │                                               │
        │   runner.py ──mounts the checkout──┐          │
        │                                    ▼          │
        │            ┌──────────────────────────────┐   │
        │            │  PINNED DOCKER SANDBOX        │   │
        │            │  Go 1.22.5 + golangci-lint    │   │
        │            │  runs `go build/test` here    │   │
        │            │  → exit code = pass/fail       │   │
        │            └──────────────────────────────┘   │
        │                                               │
        │   llm/client.py ──► Ollama (qwen2.5-coder:7b) │
        └─────────────────────────────────────────────┘
```

The **gate** (`gate-0`) is the proof that this whole picture works. It's a runnable check, not a feeling — and it's green only when the sandbox demonstrably builds a clean checkout *and* flags a deliberately broken one.

---

## 2. What Stage 0 actually did

- Stood up the repo skeleton and a swappable, provider-agnostic LLM client (talks to local Ollama via litellm).
- Built a **pinned Docker image** with a fixed Go toolchain + golangci-lint, so build/test/lint results are reproducible and independent of the host.
- Wrote the **sandbox layer**: clone/checkout a repo at a commit on the host, mount it into the container, run a command, capture and parse the result.
- Wrote a **smoke test** that proves the sandbox can both confirm a passing build and detect a failing one.
- Wrapped all of it in `gate-0`, a six-point environment check, and locked the green state as a git tag.

---

## 3. Components built (file → role)

| File | Role |
|---|---|
| `Dockerfile` | The pinned sandbox image: `golang:1.22.5` + `golangci-lint v1.61.0` + git, arch-aware (arm64/amd64). |
| `src/.../sandbox/repo.py` | Host-side git: clone into a local cache, checkout a ref, reset/clean. |
| `src/.../sandbox/runner.py` | Runs a command inside the container against the mounted checkout; returns a `CommandResult` (exit code, stdout, stderr, duration). |
| `src/.../llm/client.py` | Provider-agnostic model client (litellm → Ollama); `--ping` proves the model answers. |
| `src/.../config.py` | Loads settings from `.env` (model, API base, image tag). |
| `src/.../models.py` | `CommandResult` typed contract. |
| `src/.../cli.py` | Entry point stub (`agent --version`); subcommands come later. |
| `scripts/stage0_smoke.py` | The proof: clean build passes, corrupted build fails, tree restored. |
| `scripts/check_env.sh` | **gate-0** — the six-point environment validator. |
| `scripts/setup.sh`, `scripts/pull_models.sh` | Install deps; pull the model. |
| `Makefile` | One-word commands (`make stage0`, `setup`, `build-sandbox`, `check-env`, …). |
| `config/`, `prompts/`, `eval/`, `docs/` | Skeleton for later stages (mostly placeholders for now). |

---

## 4. What we installed and why

| Where | Thing | Why |
|---|---|---|
| **Host (Mac)** | Docker Desktop | Runs the sandbox container; the isolation + reproducibility boundary. |
| | Ollama | Serves the local LLM. |
| | conda (Python 3.11) | The harness runtime; isolated env. |
| | git | Clone/checkout target repos; version-control our own work. |
| | GitHub CLI (`gh`) | Pull real issue/PR data for ground truth (used from Stage 1). |
| | Xcode Command Line Tools | Provides `make`, `git` on macOS. |
| **Python env** | litellm | One swappable interface to any model (Ollama now, hosted later). |
| | pydantic | Typed config/contracts. |
| | python-dotenv | Load `.env`. |
| | pyyaml | Read per-repo configs. |
| | pytest, ruff (dev) | Tests + lint/format for our own code. |
| **Inside the image** | Go 1.22.5 (base image) | Compile/test the target Go repo. |
| | golangci-lint v1.61.0 | Lint gate (used from Phase 6). |
| | git, ca-certificates | VCS module fetches + TLS. |
| **Model** | `qwen2.5-coder:7b` (~4.7 GB) | Dev model that fits 16 GB comfortably; swap to a bigger one via one config line for final runs. |

---

## 5. gate-0 — the six checks (and what each proves)

| # | Check | Proves |
|---|---|---|
| 1 | `docker info` succeeds | the daemon is up and reachable |
| 2 | image `go-issue-agent-sandbox:go1.22` exists | the pinned image was built |
| 3 | `go version` in image is `go1.22.x` | the pinned toolchain is correct |
| 4 | `golangci-lint version` runs | the linter is installed |
| 5 | `llm.client --ping` returns text | the local model is reachable and answering |
| 6 | smoke: clean build exit 0 **and** corrupted build exit ≠ 0 | the sandbox runs Go on a real checkout **and** can tell pass from fail |

**Pass condition:** all six green. Result on the Mac: clean build exit 0 in ~5s, corrupted build exit 1 — ✅ green, tagged `gate-0`.

---

## 6. Gotchas we hit and how we fixed them

These are real reproducibility notes (worth keeping in the README).

| Symptom | Root cause | Fix |
|---|---|---|
| `golangci-lint: not found` after install step "succeeded" | `curl … \| sh` swallows the installer's exit code in the pipe; binary never landed | Download the pinned prebuilt binary directly and `mv` it into place |
| `Could not resolve host: github.com` during build (office server) | CNTLM proxy-only network; containers can't reach `127.0.0.1:3128`; direct DNS blocked | Moved development to a personal Mac (no proxy) |
| `fork/exec /usr/bin/nvidia-container-runtime: no such file` (office server) | Docker's *default* runtime set to `nvidia`, binary absent; no sudo to fix daemon | N/A on Mac (clean Docker Desktop) |
| `Makefile:7: missing separator` | macOS ships GNU Make 3.81, which predates `.RECIPEPREFIX` | Rewrote Makefile with real tab indentation |
| `go: command not found` in the smoke (but `go version` worked) | `bash -lc` (login shell) re-reads profiles and resets `PATH`, dropping `/usr/local/go/bin` | Use `bash -c` + explicitly prepend the Go bin dir |
| Devstral too heavy | 14 GB model on a 16 GB machine swaps/OOMs | Use `qwen2.5-coder:7b` for dev; keep the model swappable |
| `legacy builder … TARGETARCH` empty risk | Classic builder doesn't populate `TARGETARCH` | Docker Desktop uses BuildKit by default → `TARGETARCH=arm64` resolves correctly |

---

## 7. Why this matters (what Stage 0 unlocks)

- **A trust anchor.** Every future correctness claim bottoms out in "the sandbox said so," and we proved the sandbox is honest (it detects failure, not just success).
- **Reproducibility.** Pinned Go + linter + deps mean a green state stays green; the grader just needs Docker.
- **Model-independence.** None of this depends on which LLM we use, so the 7B-vs-bigger-model question never blocks progress.
- **A revertible checkpoint.** `gate-0` is a known-good state; `git reset --hard gate-0` always brings us back here.

---

## 8. Checkpoint

```bash
git init && git add -A && git commit -m "stage 0: bedrock" && git tag gate-0
make lock && git add requirements.lock && git commit -m "stage 0: pin deps"
```

---

## 9. What's next — Stage 1: ground truth

Build the **answer key**, starting with `validator-1314.json`: the real issue text, the gold one-line fix (split from any test), a reproduction test we author, and the verified `FAIL_TO_PASS` / `PASS_TO_PASS` test names. Then `gate-gt`: gold patch resolves, empty patch doesn't. Like Stage 0, it's verifiable against the `v10.24.0` checkout and independent of the model.

*(Sequence reminder: bedrock → **ground truth (Stage 1)** → eval harness/ruler (Stage 2) → tools → agent loop → reliability → polish.)*
