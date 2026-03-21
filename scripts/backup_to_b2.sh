#!/usr/bin/env bash
#
# backup_to_b2.sh — Sync CASTLINE data files to Backblaze B2
#
# Usage:
#   ./backup_to_b2.sh                  # Upload local files to B2
#   ./backup_to_b2.sh --from-vast      # SCP from Vast.ai first, then upload to B2
#   ./backup_to_b2.sh --dry-run        # Show what would be uploaded without doing it
#
# Prerequisites:
#   pip install b2[full]
#   b2 authorize-account <keyID> <applicationKey>
#
# B2 bucket: castline-data

set -euo pipefail

B2_BUCKET="castline-data"
VAST_API_KEY_FILE="$HOME/.config/vastai/vast_api_key"
LOCAL_STAGING="$HOME/Documents/fish/castline/validation/data"
TIMESTAMP=$(date +%Y-%m-%dT%H:%M:%S)

# ---------- flags ----------
FROM_VAST=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --from-vast) FROM_VAST=true ;;
        --dry-run)   DRY_RUN=true ;;
        -h|--help)
            echo "Usage: $0 [--from-vast] [--dry-run]"
            echo ""
            echo "  --from-vast   SCP files from Vast.ai instance before uploading to B2"
            echo "  --dry-run     Show what would be uploaded without actually uploading"
            echo ""
            echo "Uploads CASTLINE data, models, and logs to B2 bucket: $B2_BUCKET"
            exit 0
            ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

# ---------- helpers ----------
log()  { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

bytes_human() {
    local bytes=$1
    if   (( bytes >= 1073741824 )); then printf "%.2f GB" "$(echo "$bytes / 1073741824" | bc -l)"
    elif (( bytes >= 1048576 ));    then printf "%.1f MB" "$(echo "$bytes / 1048576" | bc -l)"
    elif (( bytes >= 1024 ));       then printf "%.1f KB" "$(echo "$bytes / 1024" | bc -l)"
    else printf "%d B" "$bytes"
    fi
}

# ---------- check b2 CLI ----------
if ! command -v b2 &>/dev/null; then
    log "b2 CLI not found. Installing..."
    pip install "b2[full]" --quiet
    if ! command -v b2 &>/dev/null; then
        fail "b2 CLI still not found after install. Check your PATH."
    fi
fi

# Verify authorization
if ! b2 get-account-info &>/dev/null 2>&1; then
    fail "b2 not authorized. Run:  b2 authorize-account <keyID> <applicationKey>"
fi
log "B2 authorized. Bucket: $B2_BUCKET"

# ---------- Vast.ai SCP ----------
if $FROM_VAST; then
    log "=== Fetching files from Vast.ai ==="

    # Get Vast instance info
    if [[ ! -f "$VAST_API_KEY_FILE" ]]; then
        fail "Vast API key not found at $VAST_API_KEY_FILE"
    fi
    VAST_KEY=$(cat "$VAST_API_KEY_FILE")

    # Get the running instance SSH info
    INSTANCE_JSON=$(vastai show instances --api-key "$VAST_KEY" --raw 2>/dev/null || true)
    if [[ -z "$INSTANCE_JSON" || "$INSTANCE_JSON" == "[]" ]]; then
        fail "No running Vast.ai instances found"
    fi

    # Parse first running instance
    SSH_HOST=$(echo "$INSTANCE_JSON" | python3 -c "
import sys, json
instances = json.load(sys.stdin)
running = [i for i in instances if i.get('actual_status') == 'running']
if not running:
    sys.exit(1)
inst = running[0]
print(f\"{inst['ssh_host']}:{inst['ssh_port']}\")
" 2>/dev/null) || fail "Could not parse Vast.ai instance SSH info"

    VAST_HOST=$(echo "$SSH_HOST" | cut -d: -f1)
    VAST_PORT=$(echo "$SSH_HOST" | cut -d: -f2)

    log "Vast.ai instance: $VAST_HOST port $VAST_PORT"

    # Remote paths on Vast.ai
    REMOTE_RAW="/workspace/castline/raw"
    REMOTE_MODELS="/workspace/castline/models"
    REMOTE_LOGS="/workspace/castline/logs"

    # Local staging directories
    STAGING_RAW="$LOCAL_STAGING/raw/vast_sync"
    STAGING_MODELS="$LOCAL_STAGING/models/vast_sync"
    STAGING_LOGS="$LOCAL_STAGING/logs"
    mkdir -p "$STAGING_RAW" "$STAGING_MODELS" "$STAGING_LOGS"

    # Critical raw data files
    RAW_FILES=(
        "tournament_events_geocoded.csv"
        "tournament_weather_daily.csv"
        "tournament_usgs_gauges.csv"
        "tournament_regime_features.csv"
    )

    log "Downloading raw data files..."
    for f in "${RAW_FILES[@]}"; do
        log "  <- $f"
        scp -P "$VAST_PORT" -o StrictHostKeyChecking=no \
            "root@${VAST_HOST}:${REMOTE_RAW}/${f}" \
            "$STAGING_RAW/" 2>/dev/null || log "  WARN: $f not found on remote"
    done

    log "Downloading model files (.cbm, .json, .pkl)..."
    scp -P "$VAST_PORT" -o StrictHostKeyChecking=no \
        "root@${VAST_HOST}:${REMOTE_MODELS}/*.cbm" \
        "root@${VAST_HOST}:${REMOTE_MODELS}/*.json" \
        "root@${VAST_HOST}:${REMOTE_MODELS}/*.pkl" \
        "$STAGING_MODELS/" 2>/dev/null || log "  WARN: some model files not found"

    log "Downloading training logs..."
    scp -P "$VAST_PORT" -o StrictHostKeyChecking=no \
        "root@${VAST_HOST}:${REMOTE_LOGS}/cpue_v5_retrain.log" \
        "$STAGING_LOGS/" 2>/dev/null || log "  WARN: training log not found"

    log "Vast.ai download complete."
    echo ""
fi

# ---------- Build upload manifest ----------
log "=== Preparing B2 upload ==="

declare -a UPLOAD_PAIRS=()
# Format: "local_path|b2_path"

add_file() {
    local local_path="$1"
    local b2_prefix="$2"
    if [[ -f "$local_path" ]]; then
        local fname=$(basename "$local_path")
        UPLOAD_PAIRS+=("${local_path}|${b2_prefix}/${fname}")
    fi
}

add_dir() {
    local local_dir="$1"
    local b2_prefix="$2"
    local pattern="$3"
    if [[ -d "$local_dir" ]]; then
        while IFS= read -r -d '' f; do
            local fname=$(basename "$f")
            UPLOAD_PAIRS+=("${f}|${b2_prefix}/${fname}")
        done < <(find "$local_dir" -maxdepth 1 -name "$pattern" -type f -print0 2>/dev/null)
    fi
}

# --- Raw data files (local project paths) ---
RAW_DIR="$HOME/Documents/fish/castline/validation/data/raw"
add_file "$RAW_DIR/tournament_events_geocoded.csv"    "raw"
add_file "$RAW_DIR/tournament_weather_daily.csv"      "raw"
add_file "$RAW_DIR/tournament_usgs_gauges.csv"        "raw"
add_file "$RAW_DIR/tournament_regime_features.csv"    "raw"
# Also pick up other important raw files
add_file "$RAW_DIR/historical_outcomes.csv"           "raw"
add_file "$RAW_DIR/all_bassmaster_outcomes.csv"       "raw"
add_file "$RAW_DIR/combined_all_outcomes_v2.csv"      "raw"
add_file "$RAW_DIR/usgs_history_full.csv"             "raw"
add_file "$RAW_DIR/weather_history_full.csv"          "raw"
add_file "$RAW_DIR/bassmaster_usgs_mapping.csv"       "raw"
add_file "$RAW_DIR/creel_cpue_bass.csv"               "raw"

# --- Vast.ai staged files (if --from-vast was used) ---
if $FROM_VAST; then
    STAGING_RAW="$LOCAL_STAGING/raw/vast_sync"
    STAGING_MODELS="$LOCAL_STAGING/models/vast_sync"
    STAGING_LOGS="$LOCAL_STAGING/logs"

    add_dir "$STAGING_RAW"    "raw"    "*.csv"
    add_dir "$STAGING_MODELS" "models" "*.cbm"
    add_dir "$STAGING_MODELS" "models" "*.json"
    add_dir "$STAGING_MODELS" "models" "*.pkl"
    add_file "$STAGING_LOGS/cpue_v5_retrain.log" "logs"
fi

# --- Model files (local) ---
MODEL_DIR="$HOME/Documents/fish/castline/validation/data/models"
add_dir "$MODEL_DIR" "models" "*.cbm"
add_dir "$MODEL_DIR" "models" "*.json"
add_dir "$MODEL_DIR" "models" "*.pkl"

# --- Assembled datasets ---
ASSEMBLED_DIR="$HOME/Documents/fish/castline/validation/data/assembled"
add_dir "$ASSEMBLED_DIR" "assembled" "*.csv"
add_dir "$ASSEMBLED_DIR" "assembled" "*.parquet"

# --- Validation artifacts ---
ARTIFACTS_DIR="$HOME/Documents/fish/castline/validation/artifacts"
add_dir "$ARTIFACTS_DIR" "artifacts" "*.json"

# ---------- Upload ----------
if [[ ${#UPLOAD_PAIRS[@]} -eq 0 ]]; then
    log "No files found to upload."
    exit 0
fi

log "Files to upload: ${#UPLOAD_PAIRS[@]}"
echo ""

TOTAL_BYTES=0
UPLOADED=0
SKIPPED=0

printf "%-60s %12s  %s\n" "FILE" "SIZE" "STATUS"
printf "%-60s %12s  %s\n" "----" "----" "------"

for pair in "${UPLOAD_PAIRS[@]}"; do
    local_path="${pair%%|*}"
    b2_path="${pair##*|}"
    fname=$(basename "$local_path")
    fsize=$(stat -f%z "$local_path" 2>/dev/null || stat -c%s "$local_path" 2>/dev/null || echo 0)
    size_str=$(bytes_human "$fsize")

    if $DRY_RUN; then
        printf "%-60s %12s  %s\n" "$b2_path" "$size_str" "[dry-run]"
        TOTAL_BYTES=$((TOTAL_BYTES + fsize))
        UPLOADED=$((UPLOADED + 1))
    else
        if b2 upload-file "$B2_BUCKET" "$local_path" "$b2_path" --quiet 2>/dev/null; then
            printf "%-60s %12s  %s\n" "$b2_path" "$size_str" "uploaded"
            TOTAL_BYTES=$((TOTAL_BYTES + fsize))
            UPLOADED=$((UPLOADED + 1))
        else
            printf "%-60s %12s  %s\n" "$b2_path" "$size_str" "FAILED"
            SKIPPED=$((SKIPPED + 1))
        fi
    fi
done

echo ""
log "=== Summary ==="
log "  Uploaded: $UPLOADED files ($(bytes_human $TOTAL_BYTES))"
[[ $SKIPPED -gt 0 ]] && log "  Failed:   $SKIPPED files"
log "  Bucket:   b2://$B2_BUCKET"
log "  Time:     $TIMESTAMP"
echo ""

if $DRY_RUN; then
    log "(Dry run -- no files were actually uploaded)"
fi
