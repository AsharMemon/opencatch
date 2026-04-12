#!/usr/bin/env bash
set -uo pipefail

LOG="/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/direct_source_wave3_retry.log"
VAST="/Users/Ashar/Library/Python/3.14/bin/vastai"
LAUNCHER="/Users/Ashar/Documents/fish/ml/bathymetry/launch_parallel_direct_source_fetch.py"

mkdir -p "$(dirname "$LOG")"

retry_ready() {
  local out
  out="$("$VAST" show instances)"
  grep -q "34656709.*running" <<<"$out" &&
    grep -q "34656712.*running" <<<"$out" &&
    grep -q "34656714.*running" <<<"$out"
}

while true; do
  if retry_ready; then
    echo "[$(date -u +%FT%TZ)] retry instances ready; launching remaining direct-source jobs" >>"$LOG"
    python3 "$LAUNCHER" \
      --skip-plan-csv /Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/parallel_direct_source_fetch_plan_launch.csv \
      --skip-plan-csv /Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/parallel_direct_source_fetch_plan_wave2.csv \
      --plan-csv /Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/parallel_direct_source_fetch_plan_wave3.csv \
      --summary-json /Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/parallel_direct_source_fetch_plan_wave3_summary.json \
      --instance-ids 34656466,34656471,34656472,34656709,34656712,34656714 \
      --launch >>"$LOG" 2>&1
    exit $?
  fi

  echo "[$(date -u +%FT%TZ)] waiting for retry instances" >>"$LOG"
  sleep 20
done
