#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="${1:?remote code dir required}"
DATA_DIR="${2:?data dir required}"
EXP_ROOT="${3:?experiment root required}"
THREADS="${4:-4}"

mkdir -p "${EXP_ROOT}"
cd "${REMOTE_DIR}"

python3 -u train_sonar_spectral.py \
  --data-dir "${DATA_DIR}" \
  --output "${EXP_ROOT}/sonar_s2_full" \
  --threads "${THREADS}" \
  > "${EXP_ROOT}/full_sonar.log" 2>&1

python3 -u train_hybrid_sonar.py \
  --data "${EXP_ROOT}/sonar_s2_full/sonar_s2_split.parquet" \
  --output "${EXP_ROOT}/hybrid_full" \
  --device cuda \
  > "${EXP_ROOT}/hybrid_full.log" 2>&1
