#!/usr/bin/env bash
set -euo pipefail

# OpenCatch — Deploy current bathymetry experiments to Vast.ai
#
# This keeps Codex experiments isolated from the main training directories by
# writing into a timestamped /data/codex_bathy_* folder.

VAST_HOST="${VAST_HOST:-ssh3.vast.ai}"
VAST_PORT="${VAST_PORT:-16546}"
VAST_USER="${VAST_USER:-root}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/root/ml/bathymetry"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_EXP="/data/codex_bathy_${STAMP}"

ssh_cmd() {
    ssh ${SSH_OPTS} -p "${VAST_PORT}" "${VAST_USER}@${VAST_HOST}" "$@"
}

scp_to() {
    scp ${SSH_OPTS} -P "${VAST_PORT}" "$1" "${VAST_USER}@${VAST_HOST}:$2"
}

echo "============================================="
echo "  OpenCatch — Bathymetry Experiment Deploy"
echo "  Host: ${VAST_USER}@${VAST_HOST}:${VAST_PORT}"
echo "  Experiment dir: ${REMOTE_EXP}"
echo "============================================="

echo ""
echo "[1/4] Testing SSH..."
ssh_cmd "echo connected && nvidia-smi --query-gpu=name,memory.total,utilization.gpu --format=csv,noheader"

echo ""
echo "[2/4] Preparing remote directories..."
ssh_cmd "mkdir -p ${REMOTE_DIR} ${REMOTE_EXP}"

echo ""
echo "[3/4] Uploading updated scripts..."
for script in \
    train_hybrid_sonar.py \
    train_v2_multimodal.py \
    finetune_depth_anything.py \
    build_s2_composites.py \
    ; do
    echo "  -> ${script}"
    scp_to "${LOCAL_DIR}/${script}" "${REMOTE_DIR}/${script}"
done

echo ""
echo "[4/4] Launching isolated training job..."
ssh_cmd "
    cd ${REMOTE_DIR}
    nohup python3 -u train_hybrid_sonar.py \
        --data /data/sonar_s2/sonar_s2_split.parquet \
        --output ${REMOTE_EXP}/hybrid_sonar \
        --device cuda \
        > ${REMOTE_EXP}/hybrid_sonar.log 2>&1 &
    echo HYBRID_PID=\$!
"

echo ""
echo "Launched hybrid sonar training."
echo "Monitor log:"
echo "  ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${REMOTE_EXP}/hybrid_sonar.log'"
echo ""
echo "Experiment root:"
echo "  ${REMOTE_EXP}"
