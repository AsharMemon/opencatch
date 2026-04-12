#!/usr/bin/env bash
set -euo pipefail

JOB_NAME="${1:?Usage: $0 <job-name> <inventory-csv> [instance-id]}"
INVENTORY_CSV="${2:?Usage: $0 <job-name> <inventory-csv> [instance-id]}"
INSTANCE_ID="${3:-34514376}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_ROOT="${REMOTE_ROOT:-/data/protected_pdf_${JOB_NAME}_${STAMP}}"
REMOTE_CODE_ROOT="/root/ml"
LOCAL_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
RUNNER="$REMOTE_CODE_ROOT/bathymetry/run_protected_pdf_fetch_vast.sh"
B2_CRED_FILE="${B2_CRED_FILE:-$HOME/.config/b2/credentials.json}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/pdf_promotion}"

if [[ ! -f "$INVENTORY_CSV" ]]; then
  echo "Missing inventory CSV: $INVENTORY_CSV" >&2
  exit 1
fi
if [[ ! -f "$B2_CRED_FILE" ]]; then
  echo "Missing B2 credentials file: $B2_CRED_FILE" >&2
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

echo "=== OpenCatch Protected PDF Fetch deploy ==="
echo "Job: $JOB_NAME"
echo "Instance: $INSTANCE_ID"
echo "SSH: root@$SSH_HOST:$SSH_PORT"
echo "Remote root: $REMOTE_ROOT"

$SSH "mkdir -p $REMOTE_CODE_ROOT/bathymetry $REMOTE_ROOT"

$SCP "$LOCAL_ROOT/ml/bathymetry/download_pdf_inventory_playwright.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/run_protected_pdf_fetch_vast.sh" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$INVENTORY_CSV" "root@$SSH_HOST:$REMOTE_ROOT/inventory.csv"

$SSH "chmod +x $RUNNER && \
  JOB_NAME='$JOB_NAME' \
  INVENTORY_PATH='$REMOTE_ROOT/inventory.csv' \
  WHERE_COLUMN='${WHERE_COLUMN:-}' \
  WHERE_VALUE='${WHERE_VALUE:-}' \
  LIMIT='${LIMIT:-}' \
  OFFSET='${OFFSET:-0}' \
  SLEEP_SECONDS='${SLEEP_SECONDS:-1.0}' \
  B2_KEY_ID='$B2_KEY_ID' \
  B2_APP_KEY='$B2_APP_KEY' \
  B2_BUCKET='$B2_BUCKET' \
  B2_PREFIX='$B2_PREFIX' \
  nohup bash $RUNNER '$REMOTE_ROOT' > '$REMOTE_ROOT/supervisor.log' 2>&1 & echo \$!"

echo "Launched remote protected PDF fetch."
echo "Tail logs:"
echo "  ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST 'tail -f $REMOTE_ROOT/supervisor.log'"
