#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${1:?usage: run_gpsnautical_inventory_vast.sh /data/run_root}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_PATH="${LOG_PATH:-$RUN_ROOT/run.log}"

CATALOG_KIND="${CATALOG_KIND:?CATALOG_KIND must be 'us' or 'ca'}"
DELAY_SECONDS="${DELAY_SECONDS:-0.15}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-60}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/gpsnautical_inventory}"
STATES_CSV="${STATES_CSV:-}"
REGIONS_CSV="${REGIONS_CSV:-}"

mkdir -p "$RUN_ROOT"
export RUN_ROOT CATALOG_KIND B2_BUCKET B2_PREFIX LOG_PATH
export B2_KEY_ID="${B2_KEY_ID:-}" B2_APP_KEY="${B2_APP_KEY:-}"
exec > >(tee -a "$LOG_PATH") 2>&1

echo "============================================"
echo "  OpenCatch GPS Nautical Discovery Crawl"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Catalog: $CATALOG_KIND"
echo "  Run root: $RUN_ROOT"
echo "============================================"

export DEBIAN_FRONTEND=noninteractive

echo ""
echo ">>> [1/3] Ensuring Python packages..."
apt-get update -qq
apt-get install -y -qq python3-pip ca-certificates >/dev/null
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet --no-cache-dir requests beautifulsoup4 b2sdk

echo ""
echo ">>> [2/3] Running discovery crawl..."
mkdir -p "$REPO_ROOT/data/bathymetry/gpsnautical"

if [ "$CATALOG_KIND" = "us" ]; then
  SCRAPE_ARGS=(
    --delay-seconds "$DELAY_SECONDS"
    --timeout "$TIMEOUT_SECONDS"
  )
  if [ -n "$STATES_CSV" ]; then
    SCRAPE_ARGS+=(--states "$STATES_CSV")
  fi
  python3 -u "$SCRIPT_DIR/scrape_gpsnautical_inventory.py" "${SCRAPE_ARGS[@]}"
  OUTPUT_FILES=(
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_state_index.csv"
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_lake_inventory.csv"
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_summary.json"
  )
elif [ "$CATALOG_KIND" = "ca" ]; then
  SCRAPE_ARGS=(
    --delay-seconds "$DELAY_SECONDS"
    --timeout "$TIMEOUT_SECONDS"
  )
  if [ -n "$REGIONS_CSV" ]; then
    SCRAPE_ARGS+=(--regions "$REGIONS_CSV")
  fi
  python3 -u "$SCRIPT_DIR/scrape_gpsnautical_canada_inventory.py" "${SCRAPE_ARGS[@]}"
  OUTPUT_FILES=(
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_ca_region_index.csv"
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_ca_lake_inventory.csv"
    "$REPO_ROOT/data/bathymetry/gpsnautical/gpsnautical_ca_summary.json"
  )
else
  echo "Unsupported CATALOG_KIND: $CATALOG_KIND" >&2
  exit 1
fi

echo ""
echo ">>> [3/3] Staging artifacts..."
for file in "${OUTPUT_FILES[@]}"; do
  if [ -f "$file" ]; then
    cp "$file" "$RUN_ROOT/"
  fi
done

echo ""
echo ">>> Uploading artifacts to B2..."
if [ -n "${B2_KEY_ID:-}" ] && [ -n "${B2_APP_KEY:-}" ]; then
  python3 - <<'PY'
import os
from pathlib import Path
from b2sdk.v2 import B2Api, InMemoryAccountInfo

run_root = Path(os.environ["RUN_ROOT"])
catalog_kind = os.environ["CATALOG_KIND"]
bucket_name = os.environ["B2_BUCKET"]
prefix = os.environ["B2_PREFIX"].rstrip("/")

files = [p for p in run_root.iterdir() if p.is_file()]
info = InMemoryAccountInfo()
api = B2Api(info)
api.authorize_account("production", os.environ["B2_KEY_ID"], os.environ["B2_APP_KEY"])
bucket = api.get_bucket_by_name(bucket_name)
for path in files:
    remote_name = f"{prefix}/{catalog_kind}/{path.name}"
    bucket.upload_local_file(local_file=str(path), file_name=remote_name)
    print(f"uploaded {path} -> b2://{bucket_name}/{remote_name}")
PY
else
  echo "B2 credentials not provided; leaving artifacts on disk only."
fi

echo ""
echo ">>> Done."
echo "Catalog: $CATALOG_KIND"
echo "Run root: $RUN_ROOT"
echo "Log: $LOG_PATH"
