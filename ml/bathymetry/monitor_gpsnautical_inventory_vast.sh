#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ] || [ $(( $# % 2 )) -ne 1 ]; then
  echo "Usage: $0 <interval-seconds> <instance-id> <remote-root> [<instance-id> <remote-root> ...]" >&2
  exit 1
fi

INTERVAL="$1"
shift
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

while true; do
  echo "===== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="
  args=("$@")
  while [ "${#args[@]}" -gt 0 ]; do
    instance_id="${args[0]}"
    remote_root="${args[1]}"
    echo "--- instance=$instance_id root=$remote_root ---"
    bash "$SCRIPT_DIR/check_gpsnautical_inventory_vast.sh" "$instance_id" "$remote_root" || true
    args=("${args[@]:2}")
  done
  sleep "$INTERVAL"
done
