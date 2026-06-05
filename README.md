# go-issue-agent

Agentic AI contributor for open-source Go projects. Given a GitHub issue from an
approved Go repo, it localizes the fix, writes a minimal patch, and validates it
by compiling/testing/linting inside a **pinned Docker sandbox**.

This snapshot is **Stage 0 (bedrock)**: the repo skeleton, the pinned (arch-aware)
Go + golangci-lint sandbox image, the local-model client (Ollama via litellm), and
the sandbox runner — plus `gate-0`, which proves the environment works end to end.

## Prerequisites (macOS / Apple Silicon)
- Docker Desktop (running; Settings -> Resources -> ~4-6 GB on a 16 GB Mac)
- Ollama (app running) with a coding model pulled
- conda (or any Python 3.11+)
- git

## Stage 0 quickstart
```bash
conda create -n go-issue-agent python=3.11 -y && conda activate go-issue-agent
make setup                          # install Python deps (editable)
cp .env.example .env                # default model is ollama/qwen2.5-coder:7b
make pull-models                    # ollama pull qwen2.5-coder:7b
make build-sandbox                  # build the pinned Go 1.22 + golangci-lint image (arm64)
make check-env                      # run gate-0
# green? lock the checkpoint:
git init && git add -A && git commit -m "stage 0: bedrock" && git tag gate-0
```

The full plan lives in `docs/architecture.md`.
