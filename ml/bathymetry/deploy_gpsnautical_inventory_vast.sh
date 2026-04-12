#!/usr/bin/env bash
set -euo pipefail

CATALOG_KIND="${1:?Usage: $0 <us|ca> [instance-id]}"
INSTANCE_ID="${2:?Usage: $0 <us|ca> [instance-id]}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_ROOT="${REMOTE_ROOT:-/data/gpsnautical_${CATALOG_KIND}_${STAMP}}"
REMOTE_CODE_ROOT="/root/ml"
LOCAL_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
RUNNER="$REMOTE_CODE_ROOT/bathymetry/run_gpsnautical_inventory_vast.sh"
B2_CRED_FILE="${B2_CRED_FILE:-$HOME/.config/b2/credentials.json}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/gpsnautical_inventory}"

if [[ "$CATALOG_KIND" != "us" && "$CATALOG_KIND" != "ca" ]]; then
  echo "CATALOG_KIND must be 'us' or 'ca'" >&2
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

echo "=== OpenCatch GPS Nautical Discovery deploy ==="
echo "Catalog: $CATALOG_KIND"
echo "Instance: $INSTANCE_ID"
echo "SSH: root@$SSH_HOST:$SSH_PORT"
echo "Remote root: $REMOTE_ROOT"

$SSH "mkdir -p $REMOTE_CODE_ROOT/bathymetry $REMOTE_ROOT"

$SCP "$LOCAL_ROOT/ml/bathymetry/run_gpsnautical_inventory_vast.sh" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/scrape_gpsnautical_inventory.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/scrape_gpsnautical_canada_inventory.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"

$SSH "chmod +x $RUNNER && \
  CATALOG_KIND='$CATALOG_KIND' \
  DELAY_SECONDS='${DELAY_SECONDS:-0.15}' \
  TIMEOUT_SECONDS='${TIMEOUT_SECONDS:-60}' \
  STATES_CSV='${STATES_CSV:-}' \
  REGIONS_CSV='${REGIONS_CSV:-}' \
  B2_KEY_ID='$B2_KEY_ID' \
  B2_APP_KEY='$B2_APP_KEY' \
  B2_BUCKET='$B2_BUCKET' \
  B2_PREFIX='$B2_PREFIX' \
  nohup bash $RUNNER '$REMOTE_ROOT' > '$REMOTE_ROOT/supervisor.log' 2>&1 & echo \$!"

echo "Launched remote GPS discovery crawl."
echo "Tail logs:"
echo "  ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST 'tail -f $REMOTE_ROOT/supervisor.log'"
echo "Remote root: $REMOTE_ROOT"
