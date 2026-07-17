#!/usr/bin/env bash
# Uploads ei_dataset_prep.py's prepared/{training,testing}/<label>/<label>.<n>.csv
# samples to an Edge Impulse project via the ingestion API (docs/
# EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.2 T2) -- curl-only, so no
# node/edge-impulse-cli install is required (neither is present on this
# dev machine).
#
# The train/test split is now decided entirely by ei_dataset_prep.py, at
# the file level (see its docstring for why) -- this script just uploads
# whatever's under the "training"/"testing" top-level folders to the
# matching EI category, it doesn't split anything itself.
#
# Batches BATCH_SIZE files per HTTP request instead of one request per file
# -- confirmed the ingestion API creates one sample per attached "data"
# part, not one merged sample -- cutting hundreds of sequential
# request round-trips down to a couple dozen.
#
# Usage:
#   EI_API_KEY=ei_xxx [BATCH_SIZE=25] ./ei_upload.sh /path/to/prepared
set -euo pipefail

: "${EI_API_KEY:?Set EI_API_KEY to your Edge Impulse project API key (Dashboard -> Keys -> Add API key)}"
PREPARED_DIR="${1:?Usage: EI_API_KEY=... ei_upload.sh <prepared-dir>}"
BATCH_SIZE="${BATCH_SIZE:-25}"

upload_batch() {
  local category="$1" label="$2"
  shift 2
  local args=()
  for f in "$@"; do
    args+=(-F "data=@${f};type=text/csv")
  done
  local tmp_body status
  tmp_body="$(mktemp)"
  status=$(curl -sS -X POST "https://ingestion.edgeimpulse.com/api/${category}/files" \
    -H "x-api-key: ${EI_API_KEY}" \
    -H "x-label: ${label}" \
    "${args[@]}" \
    -o "$tmp_body" -w "%{http_code}")
  echo "${status} ${category} ${label} batch-of-$#: $(basename "$1") .. $(basename "${@: -1}")"
  if [[ "$status" != "200" ]]; then
    cat "$tmp_body" >&2
  fi
  rm -f "$tmp_body"
}

upload_category() {
  local category="$1" label="$2"
  shift 2
  local all=("$@")
  local n=${#all[@]}
  local start
  for ((start = 0; start < n; start += BATCH_SIZE)); do
    upload_batch "$category" "$label" "${all[@]:start:BATCH_SIZE}"
  done
}

for category in training testing; do
  category_dir="$PREPARED_DIR/$category"
  if [[ ! -d "$category_dir" ]]; then
    echo "skipping $category: no $category_dir" >&2
    continue
  fi
  for class_dir in "$category_dir"/*/; do
    label="$(basename "$class_dir")"
    files=()
    while IFS= read -r f; do
      files+=("$f")
    done < <(find "$class_dir" -name '*.csv' | sort -V)
    upload_category "$category" "$label" "${files[@]}"
  done
done
