#!/usr/bin/env bash
# ==============================================================================
# AuctionBot - Production Rollback Script
# Redeploys a previous known-good Git commit or tag.
# ==============================================================================

set -euo pipefail

TARGET_REF="${1:-}"

if [ -z "$TARGET_REF" ]; then
  echo "Usage: $0 <target-git-commit-or-tag>"
  echo "Example: $0 phase-07-dashboard"
  exit 1
fi

echo "=================================================="
echo "Initiating AuctionBot Rollback"
echo "Target Ref: $TARGET_REF"
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "=================================================="

COMPOSE_FILE="docker-compose.prod.yml"

echo "[!] Checking out target ref: $TARGET_REF"
git fetch origin
git checkout "$TARGET_REF"

echo "[!] Rebuilding container images for rollback version..."
docker compose -f "$COMPOSE_FILE" build web worker

echo "[!] Restarting services with rollback version..."
docker compose -f "$COMPOSE_FILE" up -d web worker nginx
docker compose -f "$COMPOSE_FILE" restart nginx

echo "[!] Running collectstatic for rollback version..."
docker compose -f "$COMPOSE_FILE" exec -T web python manage.py collectstatic --noinput

echo "[!] Verifying health after rollback..."
docker compose -f "$COMPOSE_FILE" exec -T web python -c "
import urllib.request
resp = urllib.request.urlopen('http://127.0.0.1:8000/health/')
assert resp.getcode() == 200
print('Rollback web health check verified.')
"
docker compose -f "$COMPOSE_FILE" exec -T nginx wget -qO- --header="Host: auctionbot.shop" http://127.0.0.1/health/
echo "Rollback Nginx integration health check verified."



echo "=================================================="
echo "[✓] ROLLBACK COMPLETE: Application running on $TARGET_REF"
echo "[!] NOTE: Database schema was NOT automatically reverted."
echo "    Verify whether any migration adjustments are required."
echo "=================================================="
