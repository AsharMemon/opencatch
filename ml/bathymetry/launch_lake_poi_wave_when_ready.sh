#!/usr/bin/env bash
set -uo pipefail

LOG="/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/lake_poi_wave_retry.log"
VAST="/Users/Ashar/Library/Python/3.14/bin/vastai"
LAUNCHER="/Users/Ashar/Documents/fish/ml/bathymetry/launch_parallel_lake_poi_harvest.py"
INSTANCE_IDS=(
  34643002
  34643004
  34643006
  34643008
  34643010
  34643012
  34643015
  34643016
  34643017
  34643018
)

mkdir -p "$(dirname "$LOG")"

all_ready() {
  local out
  out="$("$VAST" show instances)"
  for id in "${INSTANCE_IDS[@]}"; do
    grep -q "${id}.*running" <<<"$out" || return 1
  done
  return 0
}

instance_csv() {
  local joined=""
  for id in "${INSTANCE_IDS[@]}"; do
    if [ -n "$joined" ]; then
      joined="${joined},"
    fi
    joined="${joined}${id}"
  done
  printf '%s' "$joined"
}

while true; do
  if all_ready; then
    echo "[$(date -u +%FT%TZ)] lake-poi shard instances ready; launching harvest wave" >>"$LOG"
    python3 "$LAUNCHER" \
      --rows-per-shard 900 \
      --max-parallel 10 \
      --country-filter US,CA \
      --delay-seconds 0.6 \
      --timeout-seconds 45 \
      --instance-ids "$(instance_csv)" \
      --launch >>"$LOG" 2>&1
    exit $?
  fi

  echo "[$(date -u +%FT%TZ)] waiting for lake-poi shard instances" >>"$LOG"
  sleep 30
done
