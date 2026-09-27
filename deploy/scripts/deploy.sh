#!/usr/bin/env bash
# ==============================================================================
# AuctionBot - Production Deployment Script
# Executes atomic, zero-downtime updates with health check validation.
# ==============================================================================

set -euo pipefail

echo "=================================================="
echo "Starting AuctionBot Production Deployment"
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "=================================================="

# 1. Verify environment configuration
if [ ! -f .env ]; then
  echo "[-] ERROR: Production .env file not found in current directory."
  echo "    Please create .env from .env.example before deploying."
  exit 1
fi

# 2. Check docker compose availability
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
  DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
  DOCKER_COMPOSE="docker-compose"
else
  echo "[-] ERROR: Docker Compose is not installed on this server."
  exit 1
fi

COMPOSE_FILE="docker-compose.prod.yml"

# 3. Pull / build container images
echo "[+] Step 1/5: Building production containers..."
$DOCKER_COMPOSE -f "$COMPOSE_FILE" build

# 4. Start internal databases and caches first
echo "[+] Step 2/5: Bringing up backing services (PostgreSQL & Redis)..."
$DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d postgres redis

# Wait for database healthcheck
echo "[+] Waiting for PostgreSQL to become healthy..."
until [ "$($DOCKER_COMPOSE -f "$COMPOSE_FILE" ps -q postgres | xargs docker inspect -f '{{.State.Health.Status}}')" == "healthy" ]; do
  sleep 2
done

# 5. Bring up web and worker services
echo "[+] Step 3/5: Launching web application, celery worker, and nginx..."
$DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d web worker nginx

# 6. Apply database migrations & collect static files
echo "[+] Step 4/5: Running database migrations against production database..."
$DOCKER_COMPOSE -f "$COMPOSE_FILE" exec -T web python manage.py migrate --noinput

echo "[+] Collecting static files..."
$DOCKER_COMPOSE -f "$COMPOSE_FILE" exec -T web python manage.py collectstatic --noinput

# 7. Verification: Run health checks
echo "[+] Step 5/5: Running health checks against production services..."
sleep 3
HEALTH_OUTPUT=$($DOCKER_COMPOSE -f "$COMPOSE_FILE" exec -T web python -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/health/') as resp:
        data = json.loads(resp.read().decode())
        if data.get('status') == 'ok':
            print('HEALTH_OK')
        else:
            print('HEALTH_DEGRADED:', data)
except Exception as e:
    print('HEALTH_FAILED:', e)
")

if [[ "$HEALTH_OUTPUT" == *"HEALTH_OK"* ]]; then
  echo "=================================================="
  echo "[✓] DEPLOYMENT SUCCESSFUL: System is HEALTHY."
  echo "=================================================="
  exit 0
else
  echo "[-] CRITICAL: Health check returned non-healthy response: $HEALTH_OUTPUT"
  echo "[-] Inspect container logs via: $DOCKER_COMPOSE -f $COMPOSE_FILE logs"
  exit 1
fi
