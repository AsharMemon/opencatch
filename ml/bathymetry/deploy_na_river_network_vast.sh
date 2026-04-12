#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${1:-33296547}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_ROOT="${REMOTE_ROOT:-/data/na_river_network_${STAMP}}"
REMOTE_CODE_ROOT="/root/ml"
LOCAL_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
RUNNER="$REMOTE_CODE_ROOT/bathymetry/run_na_river_network_vast.sh"
B2_CRED_FILE="${B2_CRED_FILE:-$HOME/.config/b2/credentials.json}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/production/tiles}"

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
p = sys.argv[1]
d = json.load(open(p))
print(d["keyID"], d["applicationKey"])
PY
)
EOF

SSH="ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST"
SCP="scp $SSH_OPTS -P $SSH_PORT"

echo "=== OpenCatch NA River Network deploy ==="
echo "Instance: $INSTANCE_ID"
echo "SSH: root@$SSH_HOST:$SSH_PORT"
echo "Remote root: $REMOTE_ROOT"

$SSH "mkdir -p $REMOTE_CODE_ROOT/bathymetry $REMOTE_CODE_ROOT/data_pipeline $REMOTE_CODE_ROOT/data/bathymetry/nrcan_hydrography $REMOTE_ROOT"

$SCP "$LOCAL_ROOT/ml/bathymetry/build_na_river_network.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/build_overlay_pmtiles.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/pmtiles_cli.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/bathymetry/run_na_river_network_vast.sh" "root@$SSH_HOST:$REMOTE_CODE_ROOT/bathymetry/"
$SCP "$LOCAL_ROOT/ml/data_pipeline/fetch_nhdplus.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/data_pipeline/"

if [[ -f "$LOCAL_ROOT/data/bathymetry/nrcan_hydrography/directory_manifest.json" ]]; then
  $SCP "$LOCAL_ROOT/data/bathymetry/nrcan_hydrography/directory_manifest.json" \
    "root@$SSH_HOST:$REMOTE_CODE_ROOT/data/bathymetry/nrcan_hydrography/"
fi
if [[ -f "$LOCAL_ROOT/data/bathymetry/nrcan_hydrography/manifest.json" ]]; then
  $SCP "$LOCAL_ROOT/data/bathymetry/nrcan_hydrography/manifest.json" \
    "root@$SSH_HOST:$REMOTE_CODE_ROOT/data/bathymetry/nrcan_hydrography/"
fi

$SSH "chmod +x $RUNNER && \
  PACKAGE_ONLY='${PACKAGE_ONLY:-0}' \
  US_HUC2='${US_HUC2:-}' \
  B2_KEY_ID='$B2_KEY_ID' \
  B2_APP_KEY='$B2_APP_KEY' \
  B2_BUCKET='$B2_BUCKET' \
  B2_PREFIX='$B2_PREFIX' \
  nohup bash $RUNNER '$REMOTE_ROOT' > '$REMOTE_ROOT/supervisor.log' 2>&1 & echo \$!"

echo "Launched remote build."
echo "Tail log:"
echo "  ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST 'tail -f $REMOTE_ROOT/supervisor.log'"
