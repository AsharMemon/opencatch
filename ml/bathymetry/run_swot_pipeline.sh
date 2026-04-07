#!/bin/bash
# OpenCatch -- Run SWOT Bathymetry Pipeline on Vast.ai GPU Instance
#
# Executes the full pipeline:
#   1. Fetch SWOT data for MN lakes via Hydrocron API
#   2. Build enhanced A-E curves (SWOT + ICESat-2 + 3D-LAKES)
#   3. Extract terrain features & train terrain prior
#   4. Train fusion model
#   5. Report honest metrics
#   6. Backup to B2

set -e

PIPELINE_DIR="/data/swot_pipeline"
cd "$PIPELINE_DIR"

echo "================================================================"
echo "  OpenCatch SWOT Bathymetry Pipeline"
echo "  $(date)"
echo "================================================================"
echo ""

# Check data availability
echo "--- Checking existing data ---"
for f in /data/3d_lakes_l1 /data/3d_lakes_with_depths.parquet /data/training/global_510k.parquet; do
    if [ -e "$f" ]; then
        echo "  FOUND: $f"
    else
        echo "  MISSING: $f"
    fi
done
echo ""

# ─── Step 1: Fetch SWOT Data ────────────────────────────────────────
echo "================================================================"
echo "  STEP 1: Fetch SWOT Data for Minnesota Lakes"
echo "================================================================"

# First try Hydrocron API (no Earthdata login needed for the API itself)
# If earthaccess discovery fails, we can manually provide PLD lake IDs
python fetch_swot_data.py \
    --method hydrocron \
    --state MN \
    --output /data/swot \
    --max-lakes 5000 \
    --rate-limit 0.3 \
    2>&1 | tee /data/swot/fetch_log.txt

echo ""
echo "SWOT fetch complete. Checking results..."
ls -la /data/swot/*.parquet 2>/dev/null || echo "  No parquet files yet"
echo ""

# ─── Step 2: Build Enhanced A-E Curves ──────────────────────────────
echo "================================================================"
echo "  STEP 2: Build Enhanced A-E Curves"
echo "================================================================"

python build_enhanced_ae.py \
    --swot /data/swot/swot_ae_curves.parquet \
    --icesat2 /data/icesat2_depths \
    --threedlakes /data/3d_lakes_l1 \
    --crosswalk /data/swot/pld_hydrolakes_crosswalk.parquet \
    --output /data/enhanced_ae \
    2>&1 | tee /data/enhanced_ae/build_log.txt

echo ""
echo "Enhanced A-E complete."
ls -la /data/enhanced_ae/*.parquet 2>/dev/null || echo "  No parquet files yet"
echo ""

# ─── Step 3: Terrain Depth Prior ────────────────────────────────────
echo "================================================================"
echo "  STEP 3: Terrain Depth Prior"
echo "================================================================"

# Check if terrain features already exist
if [ -f "/data/terrain_features.parquet" ]; then
    echo "Terrain features already extracted, skipping to training..."
else
    echo "Extracting terrain features (requires DEM tiles)..."
    if [ -d "/data/3dep" ]; then
        python terrain_depth_prior.py extract \
            --dem-dir /data/3dep \
            --lakes /data/hydrolakes/na_lakes.gpkg \
            --output /data/terrain_features.parquet \
            --buffer-m 1000 \
            2>&1 | tee /data/terrain_extract_log.txt
    else
        echo "  DEM directory /data/3dep not found, skipping terrain extraction"
        echo "  Terrain prior will be omitted from fusion model"
    fi
fi

# Train terrain prior model
if [ -f "/data/terrain_features.parquet" ]; then
    # Find ground truth file
    GT_FILE=""
    for f in /data/mn_sonar_depths.parquet /data/icesat2_depths/mn_sampled_depths.parquet /data/mn_dnr/mn_depths.parquet; do
        if [ -f "$f" ]; then
            GT_FILE="$f"
            break
        fi
    done

    if [ -n "$GT_FILE" ]; then
        echo "Training terrain prior model (ground truth: $GT_FILE)..."
        python terrain_depth_prior.py train \
            --features /data/terrain_features.parquet \
            --ground-truth "$GT_FILE" \
            --output /data/models/terrain_prior \
            --target max_depth_m \
            2>&1 | tee /data/models/terrain_prior/train_log.txt

        echo "Generating terrain predictions..."
        python terrain_depth_prior.py predict \
            --features /data/terrain_features.parquet \
            --model /data/models/terrain_prior \
            --output /data/terrain_depth_predictions.parquet
    else
        echo "  No ground truth file found, skipping terrain prior training"
    fi
else
    echo "  No terrain features, skipping terrain prior"
fi
echo ""

# ─── Step 4: Fusion Model ──────────────────────────────────────────
echo "================================================================"
echo "  STEP 4: Multi-Source Fusion Model"
echo "================================================================"

# Find ground truth
GT_FILE=""
for f in /data/mn_sonar_depths.parquet /data/icesat2_depths/mn_sampled_depths.parquet /data/mn_dnr/mn_depths.parquet; do
    if [ -f "$f" ]; then
        GT_FILE="$f"
        break
    fi
done

if [ -z "$GT_FILE" ]; then
    echo "ERROR: No ground truth file found. Cannot train fusion model."
    echo "Expected one of:"
    echo "  /data/mn_sonar_depths.parquet"
    echo "  /data/icesat2_depths/mn_sampled_depths.parquet"
    echo "  /data/mn_dnr/mn_depths.parquet"
else
    echo "Training fusion model (ground truth: $GT_FILE)..."
    python fusion_bathymetry.py \
        --swot-ae /data/enhanced_ae/enhanced_ae_metrics.parquet \
        --terrain /data/terrain_depth_predictions.parquet \
        --morphometric /data/training/global_510k.parquet \
        --spectral /data/sdb_preprocessed.parquet \
        --threedlakes /data/3d_lakes_with_depths.parquet \
        --ground-truth "$GT_FILE" \
        --target max_depth_m \
        --output /data/models/fusion \
        --n-folds 5 \
        2>&1 | tee /data/models/fusion/train_log.txt
fi
echo ""

# ─── Step 5: Report Results ────────────────────────────────────────
echo "================================================================"
echo "  STEP 5: Results Summary"
echo "================================================================"

echo ""
echo "--- SWOT Data ---"
if [ -f "/data/swot/swot_ae_curves.parquet" ]; then
    python -c "
import pandas as pd
df = pd.read_parquet('/data/swot/swot_ae_curves.parquet')
print(f'  Lakes: {df[\"lake_id\"].nunique()}')
print(f'  Total A-E points: {len(df)}')
print(f'  Avg points/lake: {len(df)/max(df[\"lake_id\"].nunique(),1):.1f}')
" 2>/dev/null || echo "  Could not read SWOT data"
fi

echo ""
echo "--- Enhanced A-E ---"
if [ -f "/data/enhanced_ae/enhanced_ae_metrics.parquet" ]; then
    python -c "
import pandas as pd
df = pd.read_parquet('/data/enhanced_ae/enhanced_ae_metrics.parquet')
print(f'  Lakes: {len(df)}')
print(f'  Avg A-E points: {df[\"n_ae_points\"].mean():.1f}')
print(f'  Avg fit R2: {df[\"fit_r2\"].mean():.3f}')
print(f'  Avg max depth: {df[\"max_depth_m\"].mean():.2f}m')
" 2>/dev/null || echo "  Could not read enhanced A-E data"
fi

echo ""
echo "--- Fusion Model ---"
if [ -f "/data/models/fusion/fusion_results.json" ]; then
    python -c "
import json
with open('/data/models/fusion/fusion_results.json') as f:
    r = json.load(f)
for name, m in r.get('overall', {}).items():
    print(f'  {name.upper()}: RMSE={m[\"rmse\"]:.2f}m, MAE={m[\"mae\"]:.2f}m, R2={m[\"r2\"]:.3f}')
print()
print('  Depth-stratified (ensemble):')
for s in r.get('depth_stratified', []):
    print(f'    {s[\"depth_range\"]:>10s}: RMSE={s[\"rmse\"]:.2f}m, N={s[\"n_lakes\"]}')
" 2>/dev/null || echo "  Could not read fusion results"
fi

echo ""

# ─── Step 6: Backup to B2 ──────────────────────────────────────────
echo "================================================================"
echo "  STEP 6: Backup to B2"
echo "================================================================"

B2_BUCKET="opencatch-data"
B2_KEY_ID="004b6da11e9f7ad0000000004"
B2_APP_KEY="K004pK63g6FSh2nPyxRw76B7ZmYgcrI"

pip install -q b2sdk 2>/dev/null

python -c "
from b2sdk.v2 import InMemoryAccountInfo, B2Api
import os, glob

info = InMemoryAccountInfo()
b2 = B2Api(info)
b2.authorize_account('production', '$B2_KEY_ID', '$B2_APP_KEY')
bucket = b2.get_bucket_by_name('$B2_BUCKET')

# Files to backup
files = []
for pattern in [
    '/data/swot/*.parquet',
    '/data/enhanced_ae/*.parquet',
    '/data/enhanced_ae/*.json',
    '/data/models/terrain_prior/*',
    '/data/models/fusion/*',
    '/data/terrain_depth_predictions.parquet',
]:
    files.extend(glob.glob(pattern))

print(f'Backing up {len(files)} files to B2...')
for f in files:
    remote_name = 'swot_bathymetry/' + os.path.relpath(f, '/data/')
    try:
        bucket.upload_local_file(local_file=f, file_name=remote_name)
        print(f'  Uploaded: {remote_name}')
    except Exception as e:
        print(f'  Failed: {remote_name} - {e}')

print('Backup complete!')
" 2>&1 || echo "B2 backup failed (non-critical)"

echo ""
echo "================================================================"
echo "  Pipeline Complete! $(date)"
echo "================================================================"
