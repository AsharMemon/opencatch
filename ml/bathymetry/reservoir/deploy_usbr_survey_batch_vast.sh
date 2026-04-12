#!/usr/bin/env bash
set -euo pipefail

JOB_NAME="${1:?Usage: $0 <job-name> <state-set> [instance-id]}"
STATE_SET="${2:?Usage: $0 <job-name> <state-set> [instance-id]}"
INSTANCE_ID="${3:-34587408}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_ROOT="${REMOTE_ROOT:-/data/${JOB_NAME}_${STAMP}}"
REMOTE_CODE_ROOT="/root/ml/bathymetry/reservoir"
LOCAL_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
B2_CRED_FILE="${B2_CRED_FILE:-$HOME/.config/b2/credentials.json}"
B2_BUCKET="${B2_BUCKET:-castline-data}"
B2_PREFIX="${B2_PREFIX:-castline/experiments/reservoir_usbr}"

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

echo "=== OpenCatch USBR survey batch deploy ==="
echo "Job: $JOB_NAME"
echo "States: $STATE_SET"
echo "Instance: $INSTANCE_ID"
echo "SSH: root@$SSH_HOST:$SSH_PORT"
echo "Remote root: $REMOTE_ROOT"

$SSH "mkdir -p $REMOTE_CODE_ROOT $REMOTE_ROOT"
$SCP "$LOCAL_ROOT/ml/bathymetry/reservoir/fetch_usbr_surveys.py" "root@$SSH_HOST:$REMOTE_CODE_ROOT/"
$SCP "$LOCAL_ROOT/ml/bathymetry/reservoir/run_usbr_survey_batch_vast.sh" "root@$SSH_HOST:$REMOTE_CODE_ROOT/"

$SSH "chmod +x $REMOTE_CODE_ROOT/run_usbr_survey_batch_vast.sh && \
  JOB_NAME='$JOB_NAME' \
  STATE_SET='$STATE_SET' \
  OUTPUT_DIR='$REMOTE_ROOT/output' \
  B2_KEY_ID='$B2_KEY_ID' \
  B2_APP_KEY='$B2_APP_KEY' \
  B2_BUCKET='$B2_BUCKET' \
  B2_PREFIX='$B2_PREFIX' \
  RUN_ROOT='$REMOTE_ROOT' \
  nohup bash $REMOTE_CODE_ROOT/run_usbr_survey_batch_vast.sh '$REMOTE_ROOT' > '$REMOTE_ROOT/supervisor.log' 2>&1 & echo \$!"

echo "Launched remote USBR survey batch."
echo "Tail logs:"
echo "  ssh $SSH_OPTS -p $SSH_PORT root@$SSH_HOST 'tail -f $REMOTE_ROOT/supervisor.log'"
