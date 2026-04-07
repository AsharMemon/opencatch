#!/bin/bash
# OpenCatch -- Deploy SWOT Bathymetry Pipeline to Vast.ai
# Instance: ssh -o StrictHostKeyChecking=no -p 16546 root@ssh3.vast.ai
#
# Usage: bash deploy_swot_pipeline.sh

set -e

HOST="root@ssh3.vast.ai"
PORT=16546
SSH_OPTS="-o StrictHostKeyChecking=no -p $PORT"
REMOTE_DIR="/data/swot_pipeline"
LOCAL_DIR="$(dirname "$0")"

echo "=== OpenCatch SWOT Bathymetry Pipeline Deployment ==="
echo "Target: $HOST:$PORT"
echo ""

# 1. Create remote directories
echo "--- Creating remote directories ---"
ssh $SSH_OPTS $HOST "mkdir -p $REMOTE_DIR /data/swot /data/enhanced_ae /data/models/terrain_prior /data/models/fusion"

# 2. Upload scripts
echo "--- Uploading scripts ---"
scp $SSH_OPTS \
    "$LOCAL_DIR/fetch_swot_data.py" \
    "$LOCAL_DIR/build_enhanced_ae.py" \
    "$LOCAL_DIR/terrain_depth_prior.py" \
    "$LOCAL_DIR/fusion_bathymetry.py" \
    "$HOST:$REMOTE_DIR/"

echo "--- Uploading run script ---"
scp $SSH_OPTS "$LOCAL_DIR/run_swot_pipeline.sh" "$HOST:$REMOTE_DIR/"

# 3. Install dependencies
echo "--- Installing Python dependencies ---"
ssh $SSH_OPTS $HOST "pip install -q earthaccess requests geopandas shapely rasterio scipy xgboost lightgbm scikit-learn pyarrow tqdm joblib 2>&1 | tail -5"

# 4. Configure Earthdata credentials
echo ""
echo "NOTE: SWOT data requires NASA Earthdata Login credentials."
echo "  1. Register at https://urs.earthdata.nasa.gov/ (if not already)"
echo "  2. Create ~/.netrc on the Vast.ai instance:"
echo "     machine urs.earthdata.nasa.gov"
echo "     login YOUR_USERNAME"
echo "     password YOUR_PASSWORD"
echo ""

# 5. Run the pipeline
echo "--- Starting pipeline ---"
ssh $SSH_OPTS $HOST "cd $REMOTE_DIR && bash run_swot_pipeline.sh 2>&1 | tee /data/swot_pipeline.log"
