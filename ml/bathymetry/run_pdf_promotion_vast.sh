#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${1:?usage: run_pdf_promotion_vast.sh /data/run_root}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_PATH="${LOG_PATH:-$RUN_ROOT/run.log}"

JOB_NAME="${JOB_NAME:-pdf_promotion}"
INVENTORY_PATH="${INVENTORY_PATH:?INVENTORY_PATH is required}"
HYDRO_REF_ROOT="${HYDRO_REF_ROOT:-/data/reference}"
HYDRO_ZIP_PATH="${HYDRO_ZIP_PATH:-$HYDRO_REF_ROOT/HydroLAKES_polys_v10.zip}"
HYDRO_SHP_DIR="${HYDRO_SHP_DIR:-$HYDRO_REF_ROOT/HydroLAKES_polys_v10_shp}"
HYDRO_SHP_PATH="${HYDRO_SHP_PATH:-$HYDRO_SHP_DIR/HydroLAKES_polys_v10.shp}"
LAKE_POLYGONS_PATH="${LAKE_POLYGONS_PATH:-}"
PDF_DIR="${PDF_DIR:-$RUN_ROOT/pdfs}"
DIGITIZED_DIR="${DIGITIZED_DIR:-$RUN_ROOT/digitized}"
MODE="${MODE:-full}"
MIN_QUALITY="${MIN_QUALITY:-0.05}"
NUM_BANDS="${NUM_BANDS:-6}"
DPI="${DPI:-300}"
WHERE_COLUMN="${WHERE_COLUMN:-}"
WHERE_VALUE="${WHERE_VALUE:-}"
REGION_CODE="${REGION_CODE:-}"
LIMIT="${LIMIT:-}"
OFFSET="${OFFSET:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-0.2}"
INSECURE="${INSECURE:-0}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/pdf_promotion}"

export RUN_ROOT JOB_NAME INVENTORY_PATH HYDRO_REF_ROOT HYDRO_ZIP_PATH HYDRO_SHP_DIR HYDRO_SHP_PATH LAKE_POLYGONS_PATH
export PDF_DIR DIGITIZED_DIR MODE MIN_QUALITY NUM_BANDS DPI WHERE_COLUMN WHERE_VALUE REGION_CODE
export LIMIT OFFSET SLEEP_SECONDS INSECURE LOG_PATH B2_BUCKET B2_PREFIX

mkdir -p "$RUN_ROOT" "$PDF_DIR" "$DIGITIZED_DIR" "$HYDRO_REF_ROOT"
exec > >(tee -a "$LOG_PATH") 2>&1

wait_for_apt() {
  local waited=0
  while fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || \
        fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
        fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do
    echo ">>> Waiting for apt/dpkg lock... (${waited}s)"
    sleep 5
    waited=$((waited + 5))
  done
}

echo "============================================"
echo "  OpenCatch PDF Bathymetry Promotion"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Job: $JOB_NAME"
echo "  Run root: $RUN_ROOT"
echo "============================================"

export DEBIAN_FRONTEND=noninteractive

echo ""
echo ">>> [1/5] Ensuring system packages..."
wait_for_apt
apt-get update -qq
wait_for_apt
apt-get install -y -qq \
  gdal-bin libgdal-dev python3-gdal \
  unzip build-essential ca-certificates >/dev/null

echo ""
echo ">>> [2/5] Ensuring Python packages..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet --no-cache-dir \
  'numpy<2' requests pandas \
  pymupdf opencv-python-headless easyocr \
  geopandas pyogrio shapely fiona pyproj \
  b2sdk

echo ""
echo ">>> [3/5] Preparing HydroLAKES reference..."
POLYGON_INPUT_PATH="$LAKE_POLYGONS_PATH"
if [ -n "$POLYGON_INPUT_PATH" ]; then
  if [ ! -f "$POLYGON_INPUT_PATH" ]; then
    echo "Missing lake polygons at $POLYGON_INPUT_PATH" >&2
    exit 1
  fi
  echo "Using provided lake polygons: $POLYGON_INPUT_PATH"
else
  if [ ! -f "$HYDRO_ZIP_PATH" ]; then
    echo "Missing HydroLAKES zip at $HYDRO_ZIP_PATH" >&2
    exit 1
  fi
  if [ ! -f "$HYDRO_SHP_PATH" ]; then
    rm -rf "$HYDRO_SHP_DIR"
    mkdir -p "$HYDRO_REF_ROOT"
    unzip -q -o "$HYDRO_ZIP_PATH" -d "$HYDRO_REF_ROOT"
  fi
  POLYGON_INPUT_PATH="$HYDRO_SHP_PATH"
fi

echo ""
echo ">>> [4/5] Downloading PDFs..."
set -- python3 "$SCRIPT_DIR/download_pdf_inventory_batch.py" \
  --inventory "$INVENTORY_PATH" \
  --output-dir "$PDF_DIR" \
  --download \
  --manifest-name download_manifest.json \
  --offset "$OFFSET" \
  --sleep-seconds "$SLEEP_SECONDS"
if [ -n "$WHERE_COLUMN" ] && [ -n "$WHERE_VALUE" ]; then
  set -- "$@" --where-column "$WHERE_COLUMN" --where-value "$WHERE_VALUE"
fi
if [ -n "$REGION_CODE" ]; then
  set -- "$@" --region-code "$REGION_CODE"
fi
if [ -n "$LIMIT" ]; then
  set -- "$@" --limit "$LIMIT"
fi
if [ "$INSECURE" = "1" ]; then
  set -- "$@" --insecure
fi
"$@"

echo ""
echo ">>> [5/5] Digitizing PDFs..."
python3 "$SCRIPT_DIR/digitize_pdf_bathymetry.py" \
  --input "$PDF_DIR" \
  --lake-polygons "$POLYGON_INPUT_PATH" \
  --name-manifest "$PDF_DIR/download_manifest.json" \
  --output "$DIGITIZED_DIR" \
  --mode "$MODE" \
  --num-bands "$NUM_BANDS" \
  --dpi "$DPI" \
  --min-quality "$MIN_QUALITY"

echo ""
echo ">>> Packaging artifacts..."
SUMMARY_JSON="$RUN_ROOT/summary.json"
ARCHIVE_PATH="$RUN_ROOT/${JOB_NAME}_digitized.tar.gz"
tar -czf "$ARCHIVE_PATH" -C "$RUN_ROOT" digitized pdfs
python3 - <<'PY' > "$SUMMARY_JSON"
import csv
import json
import os
from pathlib import Path

run_root = Path(os.environ["RUN_ROOT"])
pdf_dir = Path(os.environ["PDF_DIR"])
digitized_dir = Path(os.environ["DIGITIZED_DIR"])
quality_report = digitized_dir / "_quality_report.csv"
rows = []
if quality_report.exists():
    with quality_report.open() as f:
        rows = list(csv.DictReader(f))
scores = []
for row in rows:
    try:
        scores.append(float(row.get("score") or 0))
    except Exception:
        pass
summary = {
    "job_name": os.environ["JOB_NAME"],
    "pdf_count": len(list(pdf_dir.glob("*.pdf"))),
    "digitized_geojson_count": len(list(digitized_dir.glob("*.geojson"))),
    "quality_report_rows": len(rows),
    "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
    "max_score": round(max(scores), 4) if scores else None,
    "min_score": round(min(scores), 4) if scores else None,
}
print(json.dumps(summary, indent=2))
PY

echo ""
echo ">>> Uploading artifacts to B2..."
if [ -n "${B2_KEY_ID:-}" ] && [ -n "${B2_APP_KEY:-}" ]; then
  python3 - <<'PY'
import os
from pathlib import Path
from b2sdk.v2 import B2Api, InMemoryAccountInfo

run_root = Path(os.environ["RUN_ROOT"])
bucket_name = os.environ["B2_BUCKET"]
prefix = os.environ["B2_PREFIX"].rstrip("/")
job_name = os.environ["JOB_NAME"]
files = [
    run_root / "summary.json",
    run_root / "run.log",
    run_root / f"{job_name}_digitized.tar.gz",
    run_root / "pdfs" / "download_manifest.json",
    run_root / "digitized" / "_quality_report.csv",
]
info = InMemoryAccountInfo()
api = B2Api(info)
api.authorize_account("production", os.environ["B2_KEY_ID"], os.environ["B2_APP_KEY"])
bucket = api.get_bucket_by_name(bucket_name)
for path in files:
    if not path.exists():
      continue
    remote_name = f"{prefix}/{job_name}/{path.name}"
    bucket.upload_local_file(local_file=str(path), file_name=remote_name)
    print(f"uploaded {path} -> b2://{bucket_name}/{remote_name}")
PY
else
  echo "B2 credentials not provided; leaving artifacts on disk only."
fi

echo ""
echo ">>> Done."
echo "Summary: $SUMMARY_JSON"
echo "Archive: $ARCHIVE_PATH"
echo "Log: $LOG_PATH"
