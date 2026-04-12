#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT="${1:?usage: run_protected_pdf_fetch_vast.sh /data/run_root}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_PATH="${LOG_PATH:-$RUN_ROOT/run.log}"

JOB_NAME="${JOB_NAME:-protected_pdf_fetch}"
INVENTORY_PATH="${INVENTORY_PATH:?INVENTORY_PATH is required}"
PDF_DIR="${PDF_DIR:-$RUN_ROOT/pdfs}"
WHERE_COLUMN="${WHERE_COLUMN:-}"
WHERE_VALUE="${WHERE_VALUE:-}"
LIMIT="${LIMIT:-}"
OFFSET="${OFFSET:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1.0}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/pdf_promotion}"

export RUN_ROOT JOB_NAME INVENTORY_PATH PDF_DIR WHERE_COLUMN WHERE_VALUE LIMIT OFFSET SLEEP_SECONDS
export B2_BUCKET B2_PREFIX

mkdir -p "$RUN_ROOT" "$PDF_DIR"
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
echo "  OpenCatch Protected PDF Fetch"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Job: $JOB_NAME"
echo "  Run root: $RUN_ROOT"
echo "============================================"

export DEBIAN_FRONTEND=noninteractive
wait_for_apt
apt-get update -qq
wait_for_apt
apt-get install -y -qq \
  ca-certificates \
  libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
  libpangocairo-1.0-0 libpango-1.0-0 libcairo2 libxkbcommon0 \
  libgtk-3-0 libnss3 libnspr4 >/dev/null

python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet --no-cache-dir playwright b2sdk
python3 -m playwright install chromium

set -- python3 "$SCRIPT_DIR/download_pdf_inventory_playwright.py" \
  --inventory "$INVENTORY_PATH" \
  --output-dir "$PDF_DIR" \
  --manifest-name download_manifest.json \
  --offset "$OFFSET" \
  --sleep-seconds "$SLEEP_SECONDS" \
  --headless

if [ -n "$WHERE_COLUMN" ] && [ -n "$WHERE_VALUE" ]; then
  set -- "$@" --where-column "$WHERE_COLUMN" --where-value "$WHERE_VALUE"
fi
if [ -n "$LIMIT" ]; then
  set -- "$@" --limit "$LIMIT"
fi
"$@"

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
    run_root / "run.log",
    run_root / "pdfs" / "download_manifest.json",
]
files.extend(run_root.joinpath("pdfs").glob("*.pdf"))
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

echo ">>> Done."
