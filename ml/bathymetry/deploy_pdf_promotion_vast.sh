#!/usr/bin/env bash
set -euo pipefail

JOB_NAME="${1:?Usage: $0 <job-name> <inventory-csv> [instance-id]}"
INVENTORY_CSV="${2:?Usage: $0 <job-name> <inventory-csv> [instance-id]}"
INSTANCE_ID="${3:-33296547}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_ROOT="${REMOTE_ROOT:-/data/pdf_promotion_${JOB_NAME}_${STAMP}}"
REMOTE_CODE_ROOT="/root/ml"
LOCAL_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
RUNNER="$REMOTE_CODE_ROOT/bathymetry/run_pdf_promotion_vast.sh"
B2_CRED_FILE="${B2_CRED_FILE:-$HOME/.config/b2/credentials.json}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/pdf_promotion}"
HYDROLAKES_ZIP="${HYDROLAKES_ZIP:-$LOCAL_ROOT/data/bathymetry/global/HydroLAKES_polys_v10.zip}"
LAKE_POLYGONS_PATH="${LAKE_POLYGONS_PATH:-}"

if [[ ! -f "$INVENTORY_CSV" ]]; then
  echo "Missing inventory CSV: $INVENTORY_CSV" >&2
  exit 1
fi
if [[ ! -f "$B2_CRED_FILE" ]]; then
  echo "Missing B2 credentials file: $B2_CRED_FILE" >&2
  exit 1
fi
if [[ -z "$LAKE_POLYGONS_PATH" && ! -f "$HYDROLAKES_ZIP" ]]; then
  echo "Missing HydroLAKES zip: $HYDROLAKES_ZIP" >&2
  exit 1
fi
if [[ -n "$LAKE_POLYGONS_PATH" && ! -f "$LAKE_POLYGONS_PATH" ]]; then
  echo "Missing lake polygons file: $LAKE_POLYGONS_PATH" >&2
  exit 1
fi

SSH_URL=$(/Users/Ashar/Library/Python/3.14/bin/vastai ssh-url "$INSTANCE_ID" | tail -n 1)
SSH_HOST=$(echo "$SSH_URL" | sed -E 's#ssh://[^@]+@([^:]+):.*#\1#')
SSH_PORT=$(echo "$SSH_URL" | sed -E 's#ssh://[^@]+@[^:]+:([0-9]+)#\1#')

read -r B2_KEY_ID B2_APP_KEY <<EOF
$(python3 - "$B2_CRED_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["keyID"], d["applicationKey"])
PY
)
EOF

SSH="ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST"
SCP="scp $SSH_OPTS -P $SSH_PORT"

echo "=== OpenCatch PDF Promotion deploy ==="
echo "Job: $JOB_NAME"
echo "Instance: $INSTANCE_ID"
echo "SSH: root@$SSH_HOST:$SSH_PORT"
echo "Remote root: $REMOTE_ROOT"

$SSH "mkdir -p $REMOTE_CODE_ROOT/bathymetry $REMOTE_ROOT /data/reference"

$SCP "$LOCAL_ROOT/ml/bathymetry/download_pdf_inventory_batch.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/digitize_pdf_bathymetry.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/run_pdf_promotion_vast.sh" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$INVENTORY_CSV" "root@$SSH_HOST:$REMOTE_ROOT/inventory.csv"

REMOTE_LAKE_POLYGONS=""
if [[ -n "$LAKE_POLYGONS_PATH" ]]; then
  POLY_EXT="${LAKE_POLYGONS_PATH##*.}"
  REMOTE_LAKE_POLYGONS="$REMOTE_ROOT/lake_polygons.${POLY_EXT}"
  echo "Copying lake polygons to remote..."
  $SCP "$LAKE_POLYGONS_PATH" "root@$SSH_HOST:$REMOTE_LAKE_POLYGONS"
elif ! $SSH "[ -f /data/reference/HydroLAKES_polys_v10.zip ]"; then
  echo "Copying HydroLAKES reference to remote..."
  $SCP "$HYDROLAKES_ZIP" "root@$SSH_HOST:/data/reference/HydroLAKES_polys_v10.zip"
fi

$SSH "chmod +x $RUNNER && \
  nohup env \
    JOB_NAME='$JOB_NAME' \
    INVENTORY_PATH='$REMOTE_ROOT/inventory.csv' \
    WHERE_COLUMN='${WHERE_COLUMN:-}' \
    WHERE_VALUE='${WHERE_VALUE:-}' \
    REGION_CODE='${REGION_CODE:-}' \
    LIMIT='${LIMIT:-}' \
    OFFSET='${OFFSET:-0}' \
    MODE='${MODE:-full}' \
    MIN_QUALITY='${MIN_QUALITY:-0.05}' \
    NUM_BANDS='${NUM_BANDS:-6}' \
    DPI='${DPI:-300}' \
    SLEEP_SECONDS='${SLEEP_SECONDS:-0.2}' \
    INSECURE='${INSECURE:-0}' \
    LAKE_POLYGONS_PATH='$REMOTE_LAKE_POLYGONS' \
    B2_KEY_ID='$B2_KEY_ID' \
    B2_APP_KEY='$B2_APP_KEY' \
    B2_BUCKET='$B2_BUCKET' \
    B2_PREFIX='$B2_PREFIX' \
    bash $RUNNER '$REMOTE_ROOT' \
    >/dev/null 2>&1 </dev/null & echo \$!"

echo "Launched remote PDF promotion."
echo "Tail logs:"
echo "  ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST 'tail -f $REMOTE_ROOT/supervisor.log'"
