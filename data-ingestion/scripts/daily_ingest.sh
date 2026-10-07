#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR/.."
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="${S_SCOPE_OUTPUT_DIR:-$PROJECT_DIR/data/runs}"
mkdir -p "$OUTPUT_DIR"

PYTHONPATH="$PROJECT_DIR" python -m app.s_scope.cli ingest \
  --target "${S_SCOPE_TARGET:-2000}" \
  --output "$OUTPUT_DIR/articles_${RUN_ID}.json" \
  --index-output "$OUTPUT_DIR/articles_index_${RUN_ID}.json" \
  --concurrency "${S_SCOPE_CONCURRENCY:-8}" \
  --timeout "${S_SCOPE_TIMEOUT:-20}"
