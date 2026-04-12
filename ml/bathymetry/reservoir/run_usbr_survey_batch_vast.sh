#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${1:?usage: run_usbr_survey_batch_vast.sh /data/run_root}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_PATH="${LOG_PATH:-$RUN_ROOT/run.log}"

JOB_NAME="${JOB_NAME:-usbr_batch}"
STATE_SET="${STATE_SET:?STATE_SET is required}"
OUTPUT_DIR="${OUTPUT_DIR:-$RUN_ROOT/output}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/reservoir_usbr}"

mkdir -p "$RUN_ROOT" "$OUTPUT_DIR"
exec > >(tee -a "$LOG_PATH") 2>&1

echo "============================================"
echo "  OpenCatch USBR Survey Batch"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Job: $JOB_NAME"
echo "  States: $STATE_SET"
echo "  Run root: $RUN_ROOT"
echo "============================================"

export DEBIAN_FRONTEND=noninteractive

echo ""
echo ">>> [1/3] Ensuring Python packages..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet --no-cache-dir pandas numpy requests pyarrow b2sdk

echo ""
echo ">>> [2/3] Fetching USBR survey catalog + PDFs..."
python3 "$SCRIPT_DIR/fetch_usbr_surveys.py" --states "$STATE_SET" --output "$OUTPUT_DIR"
python3 "$SCRIPT_DIR/fetch_usbr_surveys.py" --states "$STATE_SET" --download-pdfs --output "$OUTPUT_DIR"

echo ""
echo ">>> [3/3] Packaging artifacts..."
SUMMARY_JSON="$RUN_ROOT/summary.json"
ARCHIVE_PATH="$RUN_ROOT/${JOB_NAME}.tar.gz"
tar -czf "$ARCHIVE_PATH" -C "$RUN_ROOT" output
python3 - <<'PY' > "$SUMMARY_JSON"
import json
import os
from pathlib import Path
import pandas as pd

run_root = Path(os.environ["RUN_ROOT"])
output_dir = Path(os.environ["OUTPUT_DIR"])
catalog_csv = output_dir / "usbr_survey_catalog.csv"
catalog_rows = 0
states = []
if catalog_csv.exists():
    df = pd.read_csv(catalog_csv)
    catalog_rows = len(df)
    if "state" in df.columns:
        states = sorted(set(df["state"].dropna().astype(str)))
pdf_count = len(list((output_dir / "pdfs").glob("*.pdf")))
summary = {
    "job_name": os.environ["JOB_NAME"],
    "states": states,
    "catalog_rows": catalog_rows,
    "pdf_count": pdf_count,
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
    run_root / f"{job_name}.tar.gz",
    run_root / "output" / "usbr_survey_catalog.parquet",
    run_root / "output" / "usbr_survey_catalog.csv",
    run_root / "output" / "usbr_survey_catalog.json",
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
