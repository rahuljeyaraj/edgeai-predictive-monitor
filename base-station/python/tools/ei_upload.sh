#!/usr/bin/env bash
# Uploads ei_dataset_prep.py's prepared/<label>/<label>.<n>.csv samples to
# an Edge Impulse project via the ingestion API (docs/
# EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.2 T2) -- curl-only, so no
# node/edge-impulse-cli install is required (neither is present on this
# dev machine).
#
# Usage:
#   EI_API_KEY=ei_xxx ./ei_upload.sh /path/to/prepared
#
# Splits ~80/20 per class into training/testing deterministically (every
# 5th sample, in sorted filename order, goes to testing) since there's no
# CLI --category split here.
set -euo pipefail

: "${EI_API_KEY:?Set EI_API_KEY to your Edge Impulse project API key (Dashboard -> Keys -> Add API key)}"
PREPARED_DIR="${1:?Usage: EI_API_KEY=... ei_upload.sh <prepared-dir>}"

for class_dir in "$PREPARED_DIR"/*/; do
  label="$(basename "$class_dir")"
  i=0
  while IFS= read -r f; do
    if (( i % 5 == 4 )); then
      category="testing"
    else
      category="training"
    fi
    status=$(curl -sS -X POST "https://ingestion.edgeimpulse.com/api/${category}/files" \
      -H "x-api-key: ${EI_API_KEY}" \
      -H "x-label: ${label}" \
      -F "data=@${f};type=text/csv" \
      -o /dev/null -w "%{http_code}")
    echo "${status} ${category} ${label} $(basename "$f")"
    i=$((i + 1))
  done < <(find "$class_dir" -name '*.csv' | sort -V)
done
