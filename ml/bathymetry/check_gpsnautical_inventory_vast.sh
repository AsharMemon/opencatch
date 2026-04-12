#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${1:?Usage: $0 <instance-id> <remote-root>}"
REMOTE_ROOT="${2:?Usage: $0 <instance-id> <remote-root>}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

SSH_URL=$(/Users/Ashar/Library/Python/3.14/bin/vastai ssh-url "$INSTANCE_ID" | tail -n 1)
SSH_HOST=$(echo "$SSH_URL" | sed -E 's#ssh://[^@]+@([^:]+):.*#\1#')
SSH_PORT=$(echo "$SSH_URL" | sed -E 's#ssh://[^@]+@[^:]+:([0-9]+)#\1#')

ssh $SSH_OPTS -p "$SSH_PORT" "root@$SSH_HOST" "
echo '=== host ==='
hostname
echo '=== processes ==='
pgrep -af 'scrape_gpsnautical_inventory.py|scrape_gpsnautical_canada_inventory.py|run_gpsnautical_inventory_vast.sh' || true
echo '=== files ==='
ls -lh '$REMOTE_ROOT' || true
echo '=== supervisor ==='
tail -n 20 '$REMOTE_ROOT/supervisor.log' 2>/dev/null || true
echo '=== run ==='
tail -n 20 '$REMOTE_ROOT/run.log' 2>/dev/null || true
"
