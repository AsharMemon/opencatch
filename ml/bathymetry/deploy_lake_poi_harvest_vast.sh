#!/usr/bin/env bash
set -euo pipefail

INSTANCE_HOST="${1:?usage: deploy_lake_poi_harvest_vast.sh <host> <port> <remote_root>}"
INSTANCE_PORT="${2:?usage: deploy_lake_poi_harvest_vast.sh <host> <port> <remote_root>}"
REMOTE_ROOT="${3:?usage: deploy_lake_poi_harvest_vast.sh <host> <port> <remote_root>}"

LOCAL_ROOT="/Users/Ashar/Documents/fish"
REMOTE_ML_ROOT="/root/ml"

ssh -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "root@$INSTANCE_HOST" "mkdir -p '$REMOTE_ROOT' '$REMOTE_ML_ROOT/bathymetry'"
scp -P "$INSTANCE_PORT" \
  "$LOCAL_ROOT/ml/bathymetry/harvest_lake_pois.py" \
  "$LOCAL_ROOT/ml/bathymetry/run_lake_poi_harvest_vast.sh" \
  "$LOCAL_ROOT/data/bathymetry/gpsnautical/gpsnautical_app_lake_catalog.json" \
  "root@$INSTANCE_HOST:$REMOTE_ROOT/"
ssh -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "root@$INSTANCE_HOST" "\
  cp '$REMOTE_ROOT/harvest_lake_pois.py' '$REMOTE_ML_ROOT/bathymetry/harvest_lake_pois.py' && \
  cp '$REMOTE_ROOT/run_lake_poi_harvest_vast.sh' '$REMOTE_ML_ROOT/bathymetry/run_lake_poi_harvest_vast.sh' && \
  chmod +x '$REMOTE_ML_ROOT/bathymetry/run_lake_poi_harvest_vast.sh'"
