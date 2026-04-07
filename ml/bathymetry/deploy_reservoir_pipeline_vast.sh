#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# OpenCatch — Reservoir Pipeline Deployment to Vast.ai
# ─────────────────────────────────────────────────────────────────────
# Deploys the full reservoir monitoring pipeline (terrain extrapolation,
# A-E curve fitting, storage nowcasting) to a Vast.ai GPU instance.
#
# Usage:
#   ./deploy_reservoir_pipeline_vast.sh [threads]
#   ./deploy_reservoir_pipeline_vast.sh 4
# ─────────────────────────────────────────────────────────────────────

VAST_HOST="${VAST_HOST:-ssh3.vast.ai}"
VAST_PORT="${VAST_PORT:-16546}"
VAST_USER="root"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

LOCAL_BATHY_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/root/ml/bathymetry"
REMOTE_RES_DIR="${REMOTE_DIR}/reservoir"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EXP_ROOT="/data/reservoir_pipeline_${STAMP}"
THREADS="${1:-4}"

ssh_cmd() {
  ssh ${SSH_OPTS} -p "${VAST_PORT}" "${VAST_USER}@${VAST_HOST}" "$@"
}

scp_to() {
  scp ${SSH_OPTS} -P "${VAST_PORT}" "$1" "${VAST_USER}@${VAST_HOST}:$2"
}

echo "============================================="
echo "  OpenCatch — Reservoir Pipeline"
echo "  Target: ${VAST_USER}@${VAST_HOST}:${VAST_PORT}"
echo "  Experiment root: ${EXP_ROOT}"
echo "  Threads: ${THREADS}"
echo "============================================="

echo
echo "[1/5] Checking SSH connectivity..."
ssh_cmd "echo Connected OK"

echo
echo "[2/5] Creating directories..."
ssh_cmd "mkdir -p ${REMOTE_DIR} ${REMOTE_RES_DIR} ${EXP_ROOT}"

echo
echo "[3/5] Uploading scripts..."

# Shared dependencies
for script in \
  sdb_preprocessing.py \
  build_enhanced_ae.py \
  physics_features.py \
  terrain_depth_prior.py \
  terrain_depth_model.py \
  fetch_sentinel2.py \
  fetch_swot_data.py \
  generate_contours.py \
  b2_backup.py \
  ml_curve_fitting.py \
  stage1_max_depth.py \
  ; do
  echo "  → ${script}"
  scp_to "${LOCAL_BATHY_DIR}/${script}" "${REMOTE_DIR}/${script}"
done

# Reservoir-specific modules
for script in \
  __init__.py \
  fetch_nid.py \
  fetch_nhdplus_flowlines.py \
  fetch_usbr_surveys.py \
  cross_section_extractor.py \
  reservoir_features.py \
  storage_nowcast.py \
  train_reservoir_depth.py \
  validate_reservoir.py \
  run_reservoir_pipeline.py \
  ; do
  if [ -f "${LOCAL_BATHY_DIR}/reservoir/${script}" ]; then
    echo "  → reservoir/${script}"
    scp_to "${LOCAL_BATHY_DIR}/reservoir/${script}" "${REMOTE_RES_DIR}/${script}"
  fi
done

echo
echo "[4/5] Ensuring dependencies..."
ssh_cmd "
  pip install -q \
    pystac-client rasterio numpy pandas pyarrow tqdm \
    scikit-learn pyproj xgboost lightgbm torch \
    scipy shapely geopandas fiona requests \
    >/tmp/reservoir_pip.log 2>&1 || true
  echo 'pip install complete'
  tail -3 /tmp/reservoir_pip.log || true
"

echo
echo "[5/5] Starting reservoir pipeline..."
ssh_cmd "
  cd ${REMOTE_DIR}

  # Download DEM data if not present
  if [ ! -d /data/dem ]; then
    echo 'DEM directory not found — pipeline will download on demand'
  fi

  # Download NID catalog if not present
  if [ ! -f /data/reservoir/nid_reservoir_catalog.parquet ]; then
    echo 'Fetching NID catalog...'
    python3 ${REMOTE_RES_DIR}/fetch_nid.py \
      --output /data/reservoir/nid_reservoir_catalog.parquet \
      > ${EXP_ROOT}/fetch_nid.log 2>&1 || echo 'NID fetch failed (non-fatal)'
  fi

  # Launch main pipeline
  nohup python3 -u ${REMOTE_RES_DIR}/run_reservoir_pipeline.py \
    --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \
    --dem-dir /data/dem \
    --output ${EXP_ROOT} \
    --mode train \
    --reservoirs tier1 \
    > ${EXP_ROOT}/pipeline.log 2>&1 &
  echo Pipeline started PID:\$!
  echo Monitor: tail -f ${EXP_ROOT}/pipeline.log
"

echo
echo "============================================="
echo "  Reservoir pipeline launched"
echo "  Tail log: ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${EXP_ROOT}/pipeline.log'"
echo "============================================="

# Also upload the lake production orchestrator
echo
echo "Bonus: Uploading lake production orchestrator..."
if [ -f "${LOCAL_BATHY_DIR}/run_lake_production.py" ]; then
  scp_to "${LOCAL_BATHY_DIR}/run_lake_production.py" "${REMOTE_DIR}/run_lake_production.py"
  echo "  → run_lake_production.py uploaded"
fi

echo
echo "Done! Both pipelines available on instance."
