#!/usr/bin/env bash
set -euo pipefail

VAST_HOST="ssh3.vast.ai"
VAST_PORT="16546"
VAST_USER="root"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

LOCAL_BATHY_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/root/ml/bathymetry"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EXP_ROOT="/data/codex_bathy_${STAMP}"
THREADS="${1:-4}"

ssh_cmd() {
  ssh ${SSH_OPTS} -p "${VAST_PORT}" "${VAST_USER}@${VAST_HOST}" "$@"
}

scp_to() {
  scp ${SSH_OPTS} -P "${VAST_PORT}" "$1" "${VAST_USER}@${VAST_HOST}:$2"
}

echo "============================================="
echo "  OpenCatch — Full Sonar Pipeline"
echo "  Target: ${VAST_USER}@${VAST_HOST}:${VAST_PORT}"
echo "  Experiment root: ${EXP_ROOT}"
echo "  Threads: ${THREADS}"
echo "============================================="

echo
echo "[1/4] Checking SSH..."
ssh_cmd "echo Connected"

echo
echo "[2/4] Uploading scripts..."
ssh_cmd "mkdir -p ${REMOTE_DIR} ${EXP_ROOT}"
for script in \
  train_sonar_spectral.py \
  train_hybrid_sonar.py \
  sdb_preprocessing.py \
  run_full_sonar_pipeline_remote.sh \
  run_lake_production.py \
  stage1_max_depth.py \
  terrain_depth_model.py \
  transfer_learning_bathy.py \
  ensemble_predict.py \
  ensemble_framework.py \
  generate_contours.py \
  b2_backup.py \
  ; do
  [ -f "${LOCAL_BATHY_DIR}/${script}" ] && scp_to "${LOCAL_BATHY_DIR}/${script}" "${REMOTE_DIR}/${script}"
done

echo
echo "[3/4] Ensuring dependencies..."
ssh_cmd "
  pip install -q pystac-client rasterio numpy pandas pyarrow tqdm scikit-learn pyproj xgboost lightgbm torch >/tmp/codex_sonar_pip.log 2>&1 || true
  tail -5 /tmp/codex_sonar_pip.log || true
  chmod +x ${REMOTE_DIR}/run_full_sonar_pipeline_remote.sh
"

echo
echo "[4/4] Starting full sonar pipeline..."
ssh_cmd "
  cd ${REMOTE_DIR}
  nohup bash ${REMOTE_DIR}/run_full_sonar_pipeline_remote.sh \
    ${REMOTE_DIR} \
    /data/training/v2 \
    ${EXP_ROOT} \
    ${THREADS} \
    > ${EXP_ROOT}/pipeline_supervisor.log 2>&1 &
  echo Pipeline started PID:\$!
  echo Monitor: tail -f ${EXP_ROOT}/full_sonar.log
"

echo
echo "============================================="
echo "  Pipeline launched"
echo "  Tail log: ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${EXP_ROOT}/full_sonar.log'"
echo "============================================="
