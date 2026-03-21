#!/bin/bash
# Deploy CASTLINE Docker stack to Vast.ai instance
# Usage: ./scripts/deploy_vast.sh <ssh-host> <ssh-port>
# Example: ./scripts/deploy_vast.sh ssh4.vast.ai 18256

set -euo pipefail

SSH_HOST="${1:?Usage: $0 <ssh-host> <ssh-port>}"
SSH_PORT="${2:?Usage: $0 <ssh-host> <ssh-port>}"
SSH="ssh -o StrictHostKeyChecking=no -p $SSH_PORT root@$SSH_HOST"
SCP="scp -o StrictHostKeyChecking=no -P $SSH_PORT"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== CASTLINE Docker Deploy to $SSH_HOST:$SSH_PORT ==="

# 1. Install Docker if not present
echo "--- Checking Docker..."
$SSH "command -v docker >/dev/null 2>&1 || {
    echo 'Installing Docker...';
    curl -fsSL https://get.docker.com | sh;
}"

# 2. Install Docker Compose plugin if not present
$SSH "docker compose version >/dev/null 2>&1 || {
    echo 'Installing Docker Compose plugin...';
    apt-get update && apt-get install -y docker-compose-plugin;
}"

# 3. Create workspace
$SSH "mkdir -p /workspace/castline"

# 4. Sync required files
echo "--- Syncing files..."
# Docker infra
$SCP -r "$LOCAL_ROOT/infra" "root@$SSH_HOST:/workspace/castline/"
# API code
$SCP -r "$LOCAL_ROOT/castline/api" "root@$SSH_HOST:/workspace/castline/castline/"
# Model artifacts
$SCP -r "$LOCAL_ROOT/castline/models" "root@$SSH_HOST:/workspace/castline/castline/"
# Services
$SCP -r "$LOCAL_ROOT/castline/services" "root@$SSH_HOST:/workspace/castline/castline/" 2>/dev/null || true
# __init__.py files
$SCP "$LOCAL_ROOT/castline/__init__.py" "root@$SSH_HOST:/workspace/castline/castline/" 2>/dev/null || true

# 5. Build and start
echo "--- Building and starting Docker stack..."
$SSH "cd /workspace/castline/infra && docker compose up -d --build"

# 6. Wait for health
echo "--- Waiting for API health check..."
for i in $(seq 1 30); do
    if $SSH "curl -sf http://localhost:8000/health" >/dev/null 2>&1; then
        echo "API is healthy!"
        break
    fi
    echo "  Waiting... ($i/30)"
    sleep 2
done

# 7. Status report
echo ""
echo "=== Deployment Status ==="
$SSH "cd /workspace/castline/infra && docker compose ps"
echo ""
echo "API endpoint: http://$SSH_HOST:8000"
echo "Tile server:  http://$SSH_HOST:3000"
echo "Done!"
