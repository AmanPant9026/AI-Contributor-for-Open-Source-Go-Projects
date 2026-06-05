#!/usr/bin/env bash
set -euo pipefail
# Primary dev model for a 16 GB Mac. Override with env vars if you want others.
: "${PRIMARY_TAG:=qwen2.5-coder:7b}"      # ~4.7 GB, comfortable on 16 GB
: "${STRONGER_TAG:=qwen2.5-coder:14b}"    # ~9 GB, tight on 16 GB (optional)

echo "[models] pulling primary: ${PRIMARY_TAG}"
ollama pull "${PRIMARY_TAG}"
echo "[models] (optional) stronger model: ${STRONGER_TAG} — skip on 16 GB if low on RAM"
echo "[models]   to also pull it:  STRONGER_TAG=${STRONGER_TAG} ollama pull ${STRONGER_TAG}"
echo "[models] available:"
ollama list
