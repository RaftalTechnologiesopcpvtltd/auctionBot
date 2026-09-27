# Phase 07.5 — Live Server Environment & Production Architecture

## Overview
This document specifies the target production server architecture, service containment, network isolation, security settings, and operational procedures for AuctionBot.

---

## 1. Production Architecture Overview

The production deployment runs via Docker Compose on an isolated Linux host:

```
                      INTERNET
                         │
                         ▼
        ┌──────────────────────────────────┐
        │   Nginx Reverse Proxy (Port 80) │
        │   (SSL Termination / Static/Media│
        └─────────────────┬────────────────┘
                          │
            Internal Docker Bridge Network
        ┌─────────────────┼────────────────┐
        │                 │                │
        ▼                 ▼                ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  Django Web   │ │ Celery Worker │ │ PostgreSQL 16 │
│ (Gunicorn x4) │ │ (Concurrency 4│ │ (Internal DB) │
└───────┬───────┘ └───────┬───────┘ └───────────────┘
        │                 │
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │  Redis 7 Cache  │
        │  & Broker       │
        └─────────────────┘
```

---

## 2. Service Isolation & Network Security

* **Internal Network**: All backend containers (`postgres`, `redis`, `web`, `worker`) communicate across an internal bridge network (`auctionbot_internal`).
* **Zero Public DB/Cache Exposure**: Ports 5432 (Postgres) and 6379 (Redis) are NOT bound to host ports. They are accessible exclusively within the internal Docker network.
* **Non-Root Execution**: The Django and Celery containers run as an unprivileged user (`appuser:appgroup`).
* **Storage Volumes**:
  * `postgres_prod_data`: Persistent storage for PostgreSQL data directory.
  * `redis_prod_data`: Persistent storage for Redis RDB snapshots.
  * `staticfiles_data`: Shared volume between `web` (collectstatic) and `nginx` (fast static delivery).
  * `media_data`: Shared volume for user-uploaded catalog media.

---

## 3. Production Environment Variables (`.env`)

Production secrets are kept in `/opt/auctionbot/.env` on the host server:

```ini
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<generate-strong-50-char-secret-key>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=187.77.94.137,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://187.77.94.137,https://187.77.94.137
DJANGO_SESSION_COOKIE_SECURE=False  # Set to True when HTTPS certificate is active
DJANGO_CSRF_COOKIE_SECURE=False     # Set to True when HTTPS certificate is active

# Backing services
POSTGRES_DB=auctionbot_prod_db
POSTGRES_USER=auctionbot_prod_user
POSTGRES_PASSWORD=<strong-random-db-password>
```

---

## 4. Health Checks & Verification

* **Endpoint**: `/health/`
* **Checks Performed**:
  1. PostgreSQL database connectivity (`connection.cursor().execute("SELECT 1;")`).
  2. Redis cache connectivity (`cache.set(...)` and `cache.get(...)`).
* **Response Format**:
  ```json
  {
    "status": "ok",
    "timestamp": 1727438400,
    "services": {
      "database": "ok",
      "cache": "ok"
    }
  }
  ```
* **Security**: No database passwords, Redis URIs, or environment tokens are returned in the health check payload.

---

## 5. Rollback Procedure

In the event of an operational failure after deployment:

1. **Trigger Rollback via Script**:
   ```bash
   cd /opt/auctionbot
   ./deploy/scripts/rollback.sh <target-commit-or-tag>
   ```
2. **Alternative Trigger via GitHub Actions**:
   - Go to Actions -> `AuctionBot Deploy`.
   - Click "Run workflow".
   - Enter `target_commit` (e.g., `phase-07-dashboard`).
3. **Database Considerations**:
   - Rolling back code will NOT automatically revert database migrations.
   - Non-destructive additive migrations generally maintain backward compatibility. If a schema rollback is required, run `manage.py migrate <app> <target_migration>` manually before switching code.

---

## 6. Live Test Accounts (Pre-Migration Controlled Testing)

During Phase 07.5 verification on the live server, test flows use isolated test credentials:

1. **Platform Admin**: Superuser account with global organization switching permissions.
2. **Tenant A Admin**: Admin account bound to Tenant A (`AU`). Cannot switch context.
3. **Tenant B Admin**: Admin account bound to Tenant B (`MY`). Cannot switch context.
4. **Tenant A Staff**: Operational staff account bound to Tenant A. No financial mutation rights.

> **CRITICAL**: No legacy production accounts or data are migrated during Phase 07.5. Phase 08 legacy data migration remains deferred until explicitly authorized.
