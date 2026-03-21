#!/bin/bash
# OpenCatch ML Pipeline — Vast.ai Setup Script
#
# Run this on a fresh Vast.ai GPU instance to set up the entire
# bathymetry + water body detection pipeline.
#
# Recommended instance: RTX 3090/A5000/A6000, 24GB+ VRAM, 100GB+ disk
#
# Usage:
#   scp -r ml/ root@ssh.vast.ai:/root/ml/
#   ssh root@ssh.vast.ai "bash /root/ml/setup_vast.sh"

set -e

echo "=== OpenCatch ML Pipeline Setup ==="
echo "$(date)"

# Update and install system deps
apt-get update -qq
apt-get install -y -qq gdal-bin libgdal-dev python3-gdal git wget unzip

# Install Python packages
pip install --quiet \
    torch torchvision \
    lightgbm xgboost \
    rasterio geopandas shapely fiona pyproj \
    pystac-client planetary-computer odc-stac \
    scikit-learn pandas numpy \
    matplotlib seaborn tqdm joblib \
    requests

# Create data directories
mkdir -p /data/{waterbodies,bathymetry,sentinel2,models,contours}
mkdir -p /data/training/{s2_depth_pairs,lagos}

echo ""
echo "=== Environment Ready ==="
echo ""
echo "Next steps:"
echo "1. Download LAGOS-NE data:"
echo "   wget -O /data/training/lagos/lagos_ne.csv 'https://lagoslakes.org/...' "
echo ""
echo "2. Run Stage 1 (max depth per lake):"
echo "   python /root/ml/bathymetry/stage1_max_depth.py \\"
echo "     --lagos-path /data/training/lagos/lagos_ne.csv \\"
echo "     --output /data/models/stage1"
echo ""
echo "3. Fetch water body data:"
echo "   python /root/ml/data_pipeline/fetch_nhdplus.py \\"
echo "     --country both --output /data/waterbodies"
echo ""
echo "4. Download Sentinel-2 imagery for training lakes:"
echo "   python /root/ml/bathymetry/fetch_sentinel2.py \\"
echo "     --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\"
echo "     --output /data/sentinel2"
echo ""
echo "5. Train Stage 2 U-Net:"
echo "   python /root/ml/bathymetry/predict_depth.py \\"
echo "     --data-dir /data/training/s2_depth_pairs \\"
echo "     --output-dir /data/models/stage2 \\"
echo "     --epochs 50 --device cuda"
echo ""
echo "6. Generate contours for all lakes:"
echo "   python /root/ml/bathymetry/generate_contours.py \\"
echo "     --model /data/models/stage2/best_model.pt \\"
echo "     --lakes /data/waterbodies \\"
echo "     --output /data/contours"

# Verify GPU
python3 -c "import torch; print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"NONE\"}')"

echo ""
echo "=== Setup Complete ==="
