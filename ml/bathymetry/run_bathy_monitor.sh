#!/usr/bin/env bash
set -euo pipefail

WORKDIR="/Users/Ashar/Documents/fish"
LOG_DIR="$WORKDIR/.claude/logs"
LOG_FILE="$LOG_DIR/bathy_monitor.log"

mkdir -p "$LOG_DIR"
cd "$WORKDIR"

{
  echo
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
/usr/bin/env python3 ml/bathymetry/monitor_bathy_progress.py \
  --bucket castline-data \
  --b2-prefix castline/experiments/bathymetry/codex-improvement \
  >> "$LOG_FILE" 2>&1
} >> "$LOG_FILE" 2>&1
