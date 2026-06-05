#!/usr/bin/env bash
set -euo pipefail
python -m pip install -e ".[dev]"
echo "[setup] python deps installed (editable)"
echo "[setup] next: 'make build-sandbox' then 'make pull-models' then 'make check-env'"
