#!/bin/bash
# OpenCatch ML Pipeline — Vast.ai Setup Script
#
# Provisions a fresh Vast.ai GPU instance with everything needed for:
# - Stage 1: LightGBM max-depth prediction (LAGOS-NE training data)
# - Stage 2: U-Net satellite bathymetry (Sentinel-2 + known depth pairs)
# - Data pipelines: NHDPlus water bodies, access points, Sentinel-2 imagery
#
# Recommended instance: RTX 3090/A5000/A6000, 24GB+ VRAM, 100GB+ disk
#
# Usage:
#   scp -P <port> -r ml/ root@<host>:/root/ml/
#   ssh -p <port> root@<host> "bash /root/ml/setup_vast.sh"

set -euo pipefail

LOG="/root/setup_vast.log"
exec > >(tee -a "$LOG") 2>&1

echo "============================================"
echo "  OpenCatch ML Pipeline Setup"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "============================================"

# ── 1. System Dependencies ──────────────────────────────────────────

echo ""
echo ">>> [1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    gdal-bin libgdal-dev python3-gdal \
    git wget curl unzip \
    libspatialindex-dev \
    build-essential libsqlite3-dev zlib1g-dev \
    tippecanoe 2>/dev/null || true   # tippecanoe may not be in apt

# Install tippecanoe from source if not available via apt
if ! command -v tippecanoe &>/dev/null; then
    echo ">>> Building tippecanoe from source..."
    cd /tmp
    git clone --depth 1 https://github.com/felt/tippecanoe.git 2>/dev/null || true
    cd tippecanoe
    make -j$(nproc) && make install
    cd /root
    echo ">>> tippecanoe installed: $(tippecanoe --version 2>&1 | head -1)"
fi

# ── 2. Python Packages ──────────────────────────────────────────────

echo ""
echo ">>> [2/8] Installing Python packages..."

pip install --quiet --upgrade pip

# Core ML (torch/torchvision are pre-installed in the container image)
pip install --quiet --no-cache-dir \
    segmentation-models-pytorch \
    lightgbm xgboost \
    scikit-learn

# Geospatial
pip install --quiet --no-cache-dir \
    rasterio geopandas shapely fiona pyproj \
    pystac-client planetary-computer odc-stac

# Data & utils
pip install --quiet --no-cache-dir \
    pandas numpy \
    matplotlib seaborn tqdm joblib \
    requests

# B2 CLI for Backblaze backup
pip install --quiet --no-cache-dir b2

echo ">>> Python packages installed."

# ── 3. Directory Structure ───────────────────────────────────────────

echo ""
echo ">>> [3/8] Creating data directories..."
mkdir -p /data/{waterbodies/us,waterbodies/canada}
mkdir -p /data/{sentinel2,models/stage1,models/stage2}
mkdir -p /data/{contours/geojson,contours/rasters}
mkdir -p /data/training/{lagos,s2_depth_pairs}
mkdir -p /data/access_points
mkdir -p /root/backups

# ── 4. Configure B2 CLI ─────────────────────────────────────────────

echo ""
echo ">>> [4/8] Configuring Backblaze B2..."

# B2 credentials (OpenCatch ML backups)
B2_KEY_ID="004b6da11e9f7ad0000000004"
B2_APP_KEY="K004pK63g6FSh2nPyxRw76B7ZmYgcrI"

b2 account authorize "$B2_KEY_ID" "$B2_APP_KEY" 2>/dev/null && \
    echo ">>> B2 authorized successfully." || \
    echo ">>> B2 authorization failed — check credentials."

# ── 5. Download LAGOS-NE Lake Depth Data ─────────────────────────────

echo ""
echo ">>> [5/8] Downloading LAGOS-NE / GLOBathy lake depth data..."

LAGOS_DIR="/data/training/lagos"

if [ ! -f "$LAGOS_DIR/lagos_ne.csv" ]; then
    # Try the Python fetcher first (handles EDI + GLOBathy fallback + synthetic)
    if [ -f /root/ml/data_pipeline/fetch_lagos.py ]; then
        echo ">>> Running fetch_lagos.py (EDI -> GLOBathy -> synthetic fallback)..."
        python3 /root/ml/data_pipeline/fetch_lagos.py --output "$LAGOS_DIR" || true
    fi

    # If that didn't produce a file, download GLOBathy directly
    if [ ! -f "$LAGOS_DIR/lagos_ne.csv" ]; then
        echo ">>> Downloading GLOBathy from HydroSHEDS (Zenodo)..."
        wget -q --show-progress -O "$LAGOS_DIR/globathy_hmax.csv" \
            "https://zenodo.org/record/4891611/files/GLOBathy_hmax_summary.csv" || true
        echo ">>> GLOBathy download complete. Run fetch_lagos.py to process."
    fi
else
    echo ">>> LAGOS data already exists at $LAGOS_DIR/lagos_ne.csv"
fi

# ── 6. Download NHDPlus Water Body Boundaries ────────────────────────

echo ""
echo ">>> [6/8] Downloading NHDPlus water body data..."

NHD_DIR="/data/waterbodies/us"

if [ ! -f "$NHD_DIR/all_us_waterbodies.geojson" ]; then
    # Use the Python pipeline for USGS WFS download
    if [ -f /root/ml/data_pipeline/fetch_nhdplus.py ]; then
        echo ">>> Running fetch_nhdplus.py for US water bodies..."
        echo ">>> (This downloads ~300K water bodies via USGS WFS — may take 30-60 min)"
        python3 /root/ml/data_pipeline/fetch_nhdplus.py \
            --output /data/waterbodies \
            --country us \
            --min-area 0.05 &
        NHDPLUS_PID=$!
        echo ">>> NHDPlus download running in background (PID: $NHDPLUS_PID)"
    else
        echo ">>> fetch_nhdplus.py not found — upload ml/ directory first."
    fi
else
    echo ">>> NHDPlus data already exists."
fi

# ── 7. Verify GPU ────────────────────────────────────────────────────

echo ""
echo ">>> [7/8] Verifying GPU..."
python3 -c "
import torch
if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_mem / 1e9
    print(f'  GPU: {gpu}')
    print(f'  VRAM: {mem:.1f} GB')
    print(f'  CUDA: {torch.version.cuda}')
    print(f'  PyTorch: {torch.__version__}')
else:
    print('  WARNING: No GPU detected!')
"

# Verify key packages
echo ""
echo ">>> Package versions:"
python3 -c "
import importlib
for pkg in ['lightgbm', 'rasterio', 'geopandas', 'pystac_client',
            'planetary_computer', 'segmentation_models_pytorch']:
    try:
        m = importlib.import_module(pkg)
        v = getattr(m, '__version__', 'ok')
        print(f'  {pkg}: {v}')
    except ImportError:
        print(f'  {pkg}: MISSING')
"

# ── 8. Print Next Steps ─────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "Data directories:"
echo "  /data/training/lagos/    — LAGOS-NE lake depth training data"
echo "  /data/waterbodies/us/    — NHDPlus water body polygons"
echo "  /data/sentinel2/         — Sentinel-2 imagery tiles"
echo "  /data/models/stage1/     — Stage 1 LightGBM models"
echo "  /data/models/stage2/     — Stage 2 U-Net models"
echo "  /data/contours/          — Output contour GeoJSON + PMTiles"
echo ""
echo "Training pipeline:"
echo ""
echo "  # Stage 1: Predict max depth per lake (LightGBM)"
echo "  python3 /root/ml/bathymetry/stage1_max_depth.py \\"
echo "    --lagos-path /data/training/lagos/lagos_ne.csv \\"
echo "    --output /data/models/stage1"
echo ""
echo "  # Fetch Sentinel-2 imagery"
echo "  python3 /root/ml/bathymetry/fetch_sentinel2.py \\"
echo "    --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\"
echo "    --output /data/sentinel2 --max-cloud 10 --max-lakes 100"
echo ""
echo "  # Stage 2: Train U-Net depth model"
echo "  python3 /root/ml/bathymetry/predict_depth.py \\"
echo "    --data-dir /data/training/s2_depth_pairs \\"
echo "    --output-dir /data/models/stage2 \\"
echo "    --epochs 50 --batch-size 8 --device cuda"
echo ""
echo "  # Generate contours for all lakes"
echo "  python3 /root/ml/bathymetry/generate_contours.py \\"
echo "    --model /data/models/stage2/best_model.pt \\"
echo "    --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\"
echo "    --s2-dir /data/sentinel2 \\"
echo "    --stage1-dir /data/models/stage1 \\"
echo "    --output /data/contours --device cuda --pmtiles"
echo ""
echo "Backup to B2:"
echo "  b2 sync /data/models/ b2://opencatch-ml/models/"
echo "  b2 sync /data/contours/ b2://opencatch-ml/contours/"
echo ""
echo "Setup log: $LOG"
