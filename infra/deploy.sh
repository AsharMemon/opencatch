#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# OpenCatch Production Deployment Script
# ═══════════════════════════════════════════════════════════════
#
# Deploys the full OpenCatch stack to a VPS:
#   - PostgreSQL + PostGIS
#   - Redis
#   - FastAPI prediction server
#   - Martin vector tile server
#   - Nginx reverse proxy with Let's Encrypt TLS
#   - Background worker
#
# Usage:
#   ./deploy.sh setup <user@host>     # First-time server setup
#   ./deploy.sh deploy <user@host>    # Deploy/update the stack
#   ./deploy.sh ssl <user@host>       # Provision TLS certificates
#   ./deploy.sh logs <user@host>      # View live logs
#   ./deploy.sh status <user@host>    # Check service status
#
# Prerequisites:
#   - A VPS with SSH access (DigitalOcean, Hetzner, etc.)
#   - Domain DNS pointing to the VPS:
#       api.opencatch.app -> VPS IP
#       tiles.opencatch.app -> VPS IP
#   - This script run from the infra/ directory
# ═══════════════════════════════════════════════════════════════

REMOTE_DIR="/opt/opencatch"
COMPOSE_FILE="docker-compose.prod.yml"

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Validate args ─────────────────────────────────────────────
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 {setup|deploy|ssl|logs|status} user@host"
    exit 1
fi

CMD="$1"
HOST="$2"

# ── Helper: run command on remote ─────────────────────────────
remote() {
    ssh -o StrictHostKeyChecking=accept-new "$HOST" "$@"
}

# ── setup: First-time server provisioning ─────────────────────
cmd_setup() {
    info "Setting up server at $HOST..."

    remote bash -s <<'SETUP_EOF'
set -euo pipefail

# Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "[SETUP] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Install Docker Compose plugin if not present
if ! docker compose version &>/dev/null; then
    echo "[SETUP] Installing Docker Compose plugin..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

# Create app directory
mkdir -p /opt/opencatch

# Basic firewall (ufw)
if command -v ufw &>/dev/null; then
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
fi

echo "[SETUP] Server ready."
SETUP_EOF

    info "Server setup complete."
    info "Next steps:"
    info "  1. Create .env.prod from .env.prod.example and fill in secrets"
    info "  2. Run: $0 deploy $HOST"
}

# ── deploy: Push code and start/update the stack ──────────────
cmd_deploy() {
    info "Deploying to $HOST..."

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

    # Ensure .env.prod exists
    if [[ ! -f "$SCRIPT_DIR/.env.prod" ]]; then
        error ".env.prod not found. Copy .env.prod.example and fill in secrets."
        exit 1
    fi

    # Create remote directory structure
    remote "mkdir -p $REMOTE_DIR/infra/{api,db,martin,nginx}"

    info "Syncing files..."

    # Sync infrastructure files
    rsync -avz --delete \
        "$SCRIPT_DIR/docker-compose.prod.yml" \
        "$HOST:$REMOTE_DIR/infra/"

    rsync -avz --delete \
        "$SCRIPT_DIR/api/" \
        "$HOST:$REMOTE_DIR/infra/api/"

    rsync -avz --delete \
        "$SCRIPT_DIR/db/" \
        "$HOST:$REMOTE_DIR/infra/db/"

    rsync -avz --delete \
        "$SCRIPT_DIR/martin/" \
        "$HOST:$REMOTE_DIR/infra/martin/"

    rsync -avz --delete \
        "$SCRIPT_DIR/nginx/" \
        "$HOST:$REMOTE_DIR/infra/nginx/"

    # Sync .env.prod
    rsync -avz \
        "$SCRIPT_DIR/.env.prod" \
        "$HOST:$REMOTE_DIR/infra/.env.prod"

    # Sync application code (castline package)
    rsync -avz --delete \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude 'validation/data' \
        --exclude 'validation/artifacts' \
        --exclude 'logs' \
        "$PROJECT_ROOT/castline/" \
        "$HOST:$REMOTE_DIR/castline/"

    # Sync .dockerignore
    rsync -avz \
        "$PROJECT_ROOT/.dockerignore" \
        "$HOST:$REMOTE_DIR/"

    info "Building and starting services..."

    remote bash -s <<DEPLOY_EOF
set -euo pipefail
cd $REMOTE_DIR/infra

# Build and deploy
docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# Wait for health checks
echo "Waiting for services to become healthy..."
sleep 10

docker compose -f docker-compose.prod.yml ps

# Quick health check
if curl -sf http://localhost:8000/health >/dev/null; then
    echo "[DEPLOY] API is healthy."
else
    echo "[DEPLOY] WARNING: API health check failed."
fi
DEPLOY_EOF

    info "Deployment complete."
}

# ── ssl: Provision Let's Encrypt certificates ─────────────────
cmd_ssl() {
    info "Provisioning TLS certificates on $HOST..."

    # Read domain from .env.prod
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [[ -f "$SCRIPT_DIR/.env.prod" ]]; then
        DOMAIN=$(grep '^DOMAIN=' "$SCRIPT_DIR/.env.prod" | cut -d= -f2)
        EMAIL=$(grep '^CERTBOT_EMAIL=' "$SCRIPT_DIR/.env.prod" | cut -d= -f2)
    fi
    DOMAIN="${DOMAIN:-opencatch.app}"
    EMAIL="${EMAIL:-admin@opencatch.app}"

    remote bash -s <<SSL_EOF
set -euo pipefail
cd $REMOTE_DIR/infra

# Ensure nginx is running (for ACME challenge)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d nginx

# Run certbot for initial certificate
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm certbot \
    certbot certonly --webroot \
    -w /var/www/certbot \
    -d $DOMAIN \
    -d api.$DOMAIN \
    -d tiles.$DOMAIN \
    --email $EMAIL \
    --agree-tos \
    --non-interactive

# Reload nginx to pick up new certs
docker compose -f docker-compose.prod.yml --env-file .env.prod exec nginx nginx -s reload

echo "[SSL] Certificates provisioned for $DOMAIN"
SSL_EOF

    info "TLS certificates provisioned."
}

# ── logs: Tail logs from all services ─────────────────────────
cmd_logs() {
    info "Tailing logs from $HOST..."
    remote "cd $REMOTE_DIR/infra && docker compose -f docker-compose.prod.yml logs -f --tail=100"
}

# ── status: Check service health ──────────────────────────────
cmd_status() {
    info "Checking status on $HOST..."
    remote bash -s <<STATUS_EOF
cd $REMOTE_DIR/infra
echo "=== Container Status ==="
docker compose -f docker-compose.prod.yml ps

echo ""
echo "=== API Health ==="
curl -sf http://localhost:8000/health 2>/dev/null || echo "API unreachable"

echo ""
echo "=== Tile Server ==="
curl -sf http://localhost:3000/catalog 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(f\"Tile layers: {len(d.get('tiles',{}))} sources\")
except: print('Tile server unreachable')
"

echo ""
echo "=== Disk Usage ==="
df -h / | tail -1
docker system df
STATUS_EOF
}

# ── Dispatch ──────────────────────────────────────────────────
case "$CMD" in
    setup)  cmd_setup  ;;
    deploy) cmd_deploy ;;
    ssl)    cmd_ssl    ;;
    logs)   cmd_logs   ;;
    status) cmd_status ;;
    *)
        error "Unknown command: $CMD"
        echo "Usage: $0 {setup|deploy|ssl|logs|status} user@host"
        exit 1
        ;;
esac
