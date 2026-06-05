# Pinned sandbox image. Go is pinned to 1.24 because recent validator commits
# set `go >= 1.24.0` in go.mod (and the official image runs GOTOOLCHAIN=local,
# so it won't silently fetch another toolchain). Older code still builds on 1.24.
# Arch is auto-detected (TARGETARCH) for Apple Silicon (arm64) and amd64.

FROM golang:1.24-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# pinned golangci-lint (prebuilt binary; arch auto). NOTE: we only run
# `golangci-lint version` until Phase 6; we'll bump it to a Go-1.24-aware
# release when we actually lint.
ARG GOLANGCI_LINT_VERSION=1.61.0
ARG TARGETARCH
RUN set -eux; arch="${TARGETARCH:-amd64}"; \
    curl -sSfL --retry 3 \
      "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}.tar.gz" \
      -o /tmp/glc.tar.gz; \
    tar -xzf /tmp/glc.tar.gz -C /tmp; \
    mv "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}/golangci-lint" /usr/local/bin/golangci-lint; \
    chmod +x /usr/local/bin/golangci-lint; \
    rm -rf /tmp/glc.tar.gz "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}"

RUN go version && golangci-lint version && git --version

WORKDIR /workspace
CMD ["bash"]
