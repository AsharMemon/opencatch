#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${RUN_ROOT:?RUN_ROOT required}"
CATALOG_PATH="${CATALOG_PATH:?CATALOG_PATH required}"
OUTPUT_JSONL="${OUTPUT_JSONL:-$RUN_ROOT/lake_pois.jsonl}"
SUMMARY_JSON="${SUMMARY_JSON:-$RUN_ROOT/summary.json}"
STATE_FILTER="${STATE_FILTER:-}"
COUNTRY_FILTER="${COUNTRY_FILTER:-US}"
START_INDEX="${START_INDEX:-0}"
LIMIT="${LIMIT:-0}"
DELAY_SECONDS="${DELAY_SECONDS:-0.35}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-45}"

mkdir -p "$RUN_ROOT"

echo "============================================"
echo "  OpenCatch Lake POI Harvest"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Run root: $RUN_ROOT"
echo "============================================"

python3 -m pip install --quiet --disable-pip-version-check requests

python3 -u /root/ml/bathymetry/harvest_lake_pois.py \
  --catalog "$CATALOG_PATH" \
  --output-jsonl "$OUTPUT_JSONL" \
  --summary-json "$SUMMARY_JSON" \
  --country-filter "$COUNTRY_FILTER" \
  --state-filter "$STATE_FILTER" \
  --start-index "$START_INDEX" \
  --limit "$LIMIT" \
  --delay-seconds "$DELAY_SECONDS" \
  --timeout "$TIMEOUT_SECONDS"
