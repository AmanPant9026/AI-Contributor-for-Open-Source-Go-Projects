# Pinned sandbox image. Every Go build / test / vet / lint runs in THIS image,
# so results are reproducible and independent of the host. Versions are pinned
# deliberately. Arch is auto-detected (TARGETARCH) so this builds on both
# Apple Silicon (arm64) and x86_64 (amd64).

FROM golang:1.22.5-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---- pinned golangci-lint (prebuilt binary; arch picked automatically) ----
ARG GOLANGCI_LINT_VERSION=1.61.0
ARG TARGETARCH
RUN set -eux; \
    arch="${TARGETARCH:-amd64}"; \
    curl -sSfL --retry 3 \
      "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}.tar.gz" \
      -o /tmp/glc.tar.gz; \
    tar -xzf /tmp/glc.tar.gz -C /tmp; \
    mv "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}/golangci-lint" /usr/local/bin/golangci-lint; \
    chmod +x /usr/local/bin/golangci-lint; \
    rm -rf /tmp/glc.tar.gz "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${arch}"

# fail the build early if any tool is missing / mismatched
RUN go version && golangci-lint version && git --version

WORKDIR /workspace
CMD ["bash"]
