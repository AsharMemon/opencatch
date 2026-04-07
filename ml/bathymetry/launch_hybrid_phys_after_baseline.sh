#!/usr/bin/env bash
set -euo pipefail

BASE_DATA="${1:?baseline parquet required}"
BASE_OUT="${2:?baseline output dir required}"
PHYS_DATA="${3:?physics parquet required}"
PHYS_OUT="${4:?physics output dir required}"
REMOTE_DIR="${5:-/root/ml/bathymetry}"
POLL_SEC="${6:-60}"

mkdir -p "${PHYS_OUT}"

base_cmd="python3 -u train_hybrid_sonar.py --data ${BASE_DATA} --output ${BASE_OUT}"

while pgrep -af "${base_cmd}" >/dev/null 2>&1; do
  sleep "${POLL_SEC}"
done

if [[ -f "${PHYS_OUT}/metrics.json" ]]; then
  echo "hybrid_phys already finished at ${PHYS_OUT}"
  exit 0
fi

cd "${REMOTE_DIR}"
nohup python3 -u train_hybrid_sonar.py \
  --data "${PHYS_DATA}" \
  --output "${PHYS_OUT}" \
  --device cuda \
  > "${PHYS_OUT}.log" 2>&1 < /dev/null &

echo $!
