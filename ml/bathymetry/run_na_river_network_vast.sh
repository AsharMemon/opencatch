#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${1:-/data/na_river_network_$(date -u +%Y%m%dT%H%M%SZ)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NRCAN_ROOT="$RUN_ROOT/nrcan_hydrography"
PMTILES_OUTPUT="$RUN_ROOT/na_river_network.pmtiles"
MANIFEST_OUTPUT="$RUN_ROOT/na_river_network_manifest.json"
LOG_PATH="${LOG_PATH:-$RUN_ROOT/run.log}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/production/tiles}"
US_HUC2="${US_HUC2:-}"
PACKAGE_ONLY="${PACKAGE_ONLY:-0}"

mkdir -p "$RUN_ROOT" "$NRCAN_ROOT"
export RUN_ROOT PMTILES_OUTPUT MANIFEST_OUTPUT LOG_PATH B2_BUCKET B2_PREFIX
export B2_KEY_ID="${B2_KEY_ID:-}" B2_APP_KEY="${B2_APP_KEY:-}"
exec > >(tee -a "$LOG_PATH") 2>&1

echo "============================================"
echo "  OpenCatch North America River Network"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Run root: $RUN_ROOT"
echo "============================================"

export DEBIAN_FRONTEND=noninteractive

echo ""
echo ">>> [1/4] Ensuring system packages..."
apt-get update -qq
apt-get install -y -qq \
  gdal-bin libgdal-dev python3-gdal \
  git wget curl unzip \
  build-essential ca-certificates >/dev/null

if ! command -v tippecanoe >/dev/null 2>&1; then
  echo ">>> Installing tippecanoe..."
  apt-get install -y -qq tippecanoe >/dev/null 2>&1 || true
fi
if ! command -v tippecanoe >/dev/null 2>&1; then
  echo ">>> Building tippecanoe from source..."
  rm -rf /tmp/tippecanoe
  git clone --depth 1 https://github.com/felt/tippecanoe.git /tmp/tippecanoe >/dev/null 2>&1
  make -C /tmp/tippecanoe -j"$(nproc)" >/dev/null
  make -C /tmp/tippecanoe install >/dev/null
fi

echo ""
echo ">>> [2/4] Ensuring Python packages..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet --no-cache-dir \
  requests pyarrow pandas numpy \
  geopandas pyogrio shapely fiona pyproj \
  b2sdk

echo ""
echo ">>> [3/4] Preparing NHN manifests..."
if [ -f "$REPO_ROOT/data/bathymetry/nrcan_hydrography/directory_manifest.json" ]; then
  cp "$REPO_ROOT/data/bathymetry/nrcan_hydrography/directory_manifest.json" "$NRCAN_ROOT/directory_manifest.json"
fi
if [ -f "$REPO_ROOT/data/bathymetry/nrcan_hydrography/manifest.json" ]; then
  cp "$REPO_ROOT/data/bathymetry/nrcan_hydrography/manifest.json" "$NRCAN_ROOT/manifest.json"
fi

echo ""
echo ">>> [4/4] Building PMTiles..."
BUILD_ARGS=(
  --output-root "$RUN_ROOT"
  --pmtiles-output "$PMTILES_OUTPUT"
  --nhn-root "$NRCAN_ROOT"
  --fetch-us-sequential
  --min-us-stream-order 4
  --min-unnamed-length-km 1.0
  --min-unnamed-length-m 750.0
  --scratch-root "$RUN_ROOT/scratch"
)
if [ -n "$US_HUC2" ]; then
  BUILD_ARGS+=(--us-huc2 "$US_HUC2")
fi
if [ "$PACKAGE_ONLY" = "1" ]; then
  BUILD_ARGS+=(--package-only)
fi

python3 "$SCRIPT_DIR/build_na_river_network.py" "${BUILD_ARGS[@]}"

echo ""
echo ">>> Uploading artifacts to B2..."
if [ -n "${B2_KEY_ID:-}" ] && [ -n "${B2_APP_KEY:-}" ]; then
  python3 - <<'PY'
import os
from pathlib import Path
from b2sdk.v2 import B2Api, InMemoryAccountInfo

bucket_name = os.environ["B2_BUCKET"]
prefix = os.environ["B2_PREFIX"].rstrip("/")
files = [
    Path(os.environ["PMTILES_OUTPUT"]),
    Path(os.environ["MANIFEST_OUTPUT"]),
    Path(os.environ["LOG_PATH"]),
]
info = InMemoryAccountInfo()
api = B2Api(info)
api.authorize_account("production", os.environ["B2_KEY_ID"], os.environ["B2_APP_KEY"])
bucket = api.get_bucket_by_name(bucket_name)
for path in files:
    if not path.exists():
        continue
    remote_name = f"{prefix}/{path.name}"
    bucket.upload_local_file(local_file=str(path), file_name=remote_name)
    print(f"uploaded {path} -> b2://{bucket_name}/{remote_name}")
PY
else
  echo "B2 credentials not provided; leaving artifacts on disk only."
fi

echo ""
echo ">>> Done."
echo "PMTiles: $PMTILES_OUTPUT"
echo "Manifest: $MANIFEST_OUTPUT"
echo "Log: $LOG_PATH"
