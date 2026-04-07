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
MAX_LAKES_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-lakes)
      MAX_LAKES_ARG="--max-lakes $2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

ssh_cmd() {
  ssh ${SSH_OPTS} -p "${VAST_PORT}" "${VAST_USER}@${VAST_HOST}" "$@"
}

scp_to() {
  scp ${SSH_OPTS} -P "${VAST_PORT}" "$1" "${VAST_USER}@${VAST_HOST}:$2"
}

echo "============================================="
echo "  OpenCatch — V2 Composite Rebuild"
echo "  Target: ${VAST_USER}@${VAST_HOST}:${VAST_PORT}"
echo "  Experiment root: ${EXP_ROOT}"
echo "============================================="

echo
echo "[1/4] Checking SSH..."
ssh_cmd "echo Connected"

echo
echo "[2/4] Uploading scripts..."
ssh_cmd "mkdir -p ${REMOTE_DIR} ${EXP_ROOT}"
scp_to "${LOCAL_BATHY_DIR}/build_s2_composites.py" "${REMOTE_DIR}/build_s2_composites.py"
scp_to "${LOCAL_BATHY_DIR}/prepare_v2_rebuild_dataset.py" "${REMOTE_DIR}/prepare_v2_rebuild_dataset.py"

echo
echo "[3/4] Ensuring dependencies + preparing isolated dataset..."
ssh_cmd "
  pip install -q geopandas shapely rasterio pystac-client planetary-computer odc-stac tqdm scipy pyarrow >/tmp/codex_v2_pip.log 2>&1 || true
  tail -5 /tmp/codex_v2_pip.log || true
  python3 ${REMOTE_DIR}/prepare_v2_rebuild_dataset.py \
    --source-dir /data/training/v2 \
    --output-dir ${EXP_ROOT}/training_v2_fixed \
    --index-output ${EXP_ROOT}/training_v2_lakes.geojson \
    ${MAX_LAKES_ARG}
"

echo
echo "[4/4] Starting composite rebuild..."
ssh_cmd "
  cd ${REMOTE_DIR}
  nohup python3 -u build_s2_composites.py \
    --lakes ${EXP_ROOT}/training_v2_lakes.geojson \
    --output ${EXP_ROOT}/training_v2_fixed \
    > ${EXP_ROOT}/rebuild_composites.log 2>&1 &
  echo Rebuild started PID:\$!
  echo Monitor: tail -f ${EXP_ROOT}/rebuild_composites.log
"

echo
echo "============================================="
echo "  Rebuild launched"
echo "  Tail log: ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${EXP_ROOT}/rebuild_composites.log'"
echo "============================================="
