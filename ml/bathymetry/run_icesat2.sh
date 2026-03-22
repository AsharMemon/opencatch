#!/bin/bash
set -e
cd /root/ml/bathymetry

# Install deps
pip install efficient-kan sliderule -q 2>/dev/null || true

# Kill old training
pkill -9 -f finetune 2>/dev/null || true
sleep 1

echo "Starting ICESat-2 depth fetch..."
python3 fetch_icesat2_depths.py --output /data/icesat2_depths --regions mn wi mi 2>&1 | tee /data/icesat2_fetch.log

echo "Starting KAN training..."
python3 train_icesat2_fusion.py \
  --model kan \
  --s2-dir /data/training/v2 \
  --icesat2-dir /data/icesat2_depths \
  --output /data/models/icesat2_kan \
  --epochs 200 \
  --batch-size 256 \
  --device cuda 2>&1 | tee /data/icesat2_train.log
