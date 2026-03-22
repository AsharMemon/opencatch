#!/usr/bin/env bash
set -euo pipefail
#
# OpenCatch — Deploy ICESat-2 Fusion Model to Vast.ai
#
# Uploads training scripts and starts the ICESat-2 + S2 fusion pipeline.
# Assumes ICESat-2 depth data and S2 composites are already on the instance
# (or fetches them as part of the pipeline).
#
# Usage:
#   # Deploy and train KAN model
#   bash deploy_icesat2_vast.sh
#
#   # Deploy and train DAV2 pointwise model
#   bash deploy_icesat2_vast.sh --model dav2
#
# Prerequisites:
#   - Vast.ai instance running with SSH access
#   - ICESat-2 data fetched (or will be fetched on instance)
#   - S2 composites built (or will be built on instance)

# ── Configuration ────────────────────────────────────────────────────

VAST_HOST="ssh3.vast.ai"
VAST_PORT="16546"
VAST_USER="root"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

LOCAL_BATHY_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/workspace/bathymetry"
REMOTE_DATA="/data"

MODEL="${1:-kan}"

echo "============================================="
echo "  OpenCatch — ICESat-2 Fusion Deployment"
echo "  Model: ${MODEL}"
echo "  Target: ${VAST_USER}@${VAST_HOST}:${VAST_PORT}"
echo "============================================="

# ── Helper functions ─────────────────────────────────────────────────

ssh_cmd() {
    ssh ${SSH_OPTS} -p "${VAST_PORT}" "${VAST_USER}@${VAST_HOST}" "$@"
}

scp_to() {
    scp ${SSH_OPTS} -P "${VAST_PORT}" "$1" "${VAST_USER}@${VAST_HOST}:$2"
}

# ── Step 1: Test SSH connectivity ────────────────────────────────────

echo ""
echo "[1/6] Testing SSH connection..."
if ! ssh_cmd "echo 'Connected to Vast.ai instance'" 2>/dev/null; then
    echo "ERROR: Cannot connect to ${VAST_HOST}:${VAST_PORT}"
    echo "Check that the instance is running: vastai show instances"
    exit 1
fi

# Show GPU info
echo ""
ssh_cmd "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader" 2>/dev/null || true

# ── Step 2: Kill existing DA V2 training (plateaued) ─────────────────

echo ""
echo "[2/6] Checking for existing training processes..."
ssh_cmd "
    # Kill existing DA V2 training if running
    PIDS=\$(pgrep -f 'finetune_depth_anything' || true)
    if [ -n \"\$PIDS\" ]; then
        echo 'Killing plateaued DA V2 training (PIDs: '\$PIDS')'
        kill \$PIDS 2>/dev/null || true
        sleep 2
    else
        echo 'No existing DA V2 training found'
    fi

    # Also check for old train_v2_multimodal
    PIDS2=\$(pgrep -f 'train_v2_multimodal' || true)
    if [ -n \"\$PIDS2\" ]; then
        echo 'Killing old V2 multimodal training (PIDs: '\$PIDS2')'
        kill \$PIDS2 2>/dev/null || true
    fi
"

# ── Step 3: Upload training scripts ─────────────────────────────────

echo ""
echo "[3/6] Uploading training scripts..."
ssh_cmd "mkdir -p ${REMOTE_DIR}"

# Upload all relevant scripts
for script in \
    train_icesat2_fusion.py \
    fetch_icesat2_depths.py \
    fetch_icesat2.py \
    build_s2_composites.py \
    ; do
    if [ -f "${LOCAL_BATHY_DIR}/${script}" ]; then
        echo "  Uploading ${script}..."
        scp_to "${LOCAL_BATHY_DIR}/${script}" "${REMOTE_DIR}/${script}"
    fi
done

echo "  Scripts uploaded to ${REMOTE_DIR}/"

# ── Step 4: Install dependencies ─────────────────────────────────────

echo ""
echo "[4/6] Installing Python dependencies..."
ssh_cmd "
    pip install -q --upgrade pip
    pip install -q \
        efficient-kan \
        sliderule \
        geopandas \
        pyarrow \
        rasterio \
        pystac-client \
        planetary-computer \
        odc-stac \
        tqdm \
        transformers \
        torch torchvision \
        2>&1 | tail -5
    echo 'Dependencies installed'
"

# ── Step 5: Fetch ICESat-2 data if not present ───────────────────────

echo ""
echo "[5/6] Checking for ICESat-2 depth data..."
ssh_cmd "
    ICESAT_DIR=${REMOTE_DATA}/icesat2
    mkdir -p \${ICESAT_DIR}

    N_FILES=\$(ls \${ICESAT_DIR}/*depths*.parquet 2>/dev/null | wc -l)
    if [ \"\${N_FILES}\" -gt 0 ]; then
        echo \"Found \${N_FILES} ICESat-2 depth files\"
    else
        echo 'No ICESat-2 depth data found. Fetching priority regions...'
        cd ${REMOTE_DIR}
        python fetch_icesat2_depths.py \
            --output \${ICESAT_DIR} \
            --regions mn_north mn_south wi_north great_lakes \
            --method atl06 \
            --max-depth 30 \
            2>&1 | tail -20

        # Also try ATL13 direct
        echo 'Also trying ATL13 direct query...'
        python fetch_icesat2_depths.py \
            --output \${ICESAT_DIR} \
            --regions mn_north mn_south \
            --method atl13 \
            2>&1 | tail -10
    fi
"

# ── Step 6: Start training ───────────────────────────────────────────

echo ""
echo "[6/6] Starting ICESat-2 fusion training (${MODEL})..."

if [ "${MODEL}" = "kan" ]; then
    ssh_cmd "
        cd ${REMOTE_DIR}
        nohup python -u train_icesat2_fusion.py \
            --model kan \
            --icesat2-dir ${REMOTE_DATA}/icesat2 \
            --s2-dir ${REMOTE_DATA}/training/v2 \
            --output ${REMOTE_DATA}/models/icesat2_fusion \
            --epochs 200 \
            --batch-size 512 \
            --lr 1e-3 \
            --hidden-dims 256 128 64 32 \
            --grid-size 5 \
            --physics-weight 0.1 \
            --device cuda \
            > ${REMOTE_DATA}/models/icesat2_fusion/train_kan.log 2>&1 &

        echo \"KAN training started (PID: \$!)\"
        echo \"Monitor: ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${REMOTE_DATA}/models/icesat2_fusion/train_kan.log'\"
    "
elif [ "${MODEL}" = "dav2" ]; then
    ssh_cmd "
        cd ${REMOTE_DIR}
        nohup python -u train_icesat2_fusion.py \
            --model dav2 \
            --icesat2-dir ${REMOTE_DATA}/icesat2 \
            --s2-dir ${REMOTE_DATA}/training/v2 \
            --output ${REMOTE_DATA}/models/icesat2_fusion \
            --model-size Small \
            --patch-size 64 \
            --frozen-epochs 10 \
            --finetune-epochs 40 \
            --batch-size 16 \
            --lr 1e-3 \
            --device cuda \
            > ${REMOTE_DATA}/models/icesat2_fusion/train_dav2.log 2>&1 &

        echo \"DA V2 pointwise training started (PID: \$!)\"
        echo \"Monitor: ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} 'tail -f ${REMOTE_DATA}/models/icesat2_fusion/train_dav2.log'\"
    "
else
    echo "Unknown model: ${MODEL}. Use 'kan' or 'dav2'."
    exit 1
fi

echo ""
echo "============================================="
echo "  Deployment complete!"
echo ""
echo "  Monitor training:"
echo "    ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST}"
echo "    tail -f ${REMOTE_DATA}/models/icesat2_fusion/train_${MODEL}.log"
echo ""
echo "  Check GPU usage:"
echo "    ssh ${SSH_OPTS} -p ${VAST_PORT} ${VAST_USER}@${VAST_HOST} nvidia-smi"
echo ""
echo "  Download best model:"
echo "    scp ${SSH_OPTS} -P ${VAST_PORT} ${VAST_USER}@${VAST_HOST}:${REMOTE_DATA}/models/icesat2_fusion/best_kan_model.pt ."
echo "============================================="
