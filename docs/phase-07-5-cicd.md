# Phase 07.5 — Continuous Integration & Continuous Deployment (CI/CD)

## Overview
This document describes the automated CI/CD pipeline established for AuctionBot using GitHub Actions.

---

## 1. Pipeline Architecture

```
Developer
    ↓
git push origin main
    ↓
GitHub Actions: AuctionBot CI (.github/workflows/ci.yml)
    ├── Ubuntu 24.04 Runner
    ├── Service Container: PostgreSQL 16 (Port 5432)
    ├── Service Container: Redis 7 (Port 6379)
    ├── Python 3.11 Setup & Pip Cache
    ├── Install Dependencies (requirements/development.txt)
    ├── Django System Checks (python manage.py check)
    ├── Migration Drift Verification (makemigrations --check --dry-run)
    └── Automated Test Suite (python manage.py test)
    ↓ [On CI Success]
GitHub Actions: AuctionBot Deploy (.github/workflows/deploy.yml)
    ├── SSH via Private Key (appleboy/ssh-action)
    ├── Fetch latest release in /opt/auctionbot
    ├── Build production container images (docker compose -f docker-compose.prod.yml build)
    ├── Apply database migrations (python manage.py migrate --noinput)
    ├── Collect static assets (python manage.py collectstatic --noinput)
    └── Validate Health Check (GET /health/ == 200 OK)
```

---

## 2. CI Specifications (`.github/workflows/ci.yml`)

* **Triggers**: On push or pull request to branch `main`.
* **Database Isolation**: Dedicated PostgreSQL 16 Alpine container with isolated test database credentials. Never connects to production database.
* **Cache Isolation**: Dedicated Redis 7 Alpine container for Celery and caching. Never connects to production Redis.
* **Celery in CI**: Tests execute with `CELERY_TASK_ALWAYS_EAGER=True` to guarantee synchronous, deterministic task execution.
* **Checks Enforced**:
  1. `python manage.py check`: Validates model definitions, URL routing, and Django settings.
  2. `python manage.py makemigrations --check --dry-run`: Detects any unapplied model changes or migration drift.
  3. `python manage.py test`: Executes all 126+ automated unit and integration tests.

---

## 3. CD Deployment Specifications (`.github/workflows/deploy.yml`)

* **Triggers**: Automatically upon successful completion of `AuctionBot CI`, or manually via `workflow_dispatch`.
* **Authentication**: SSH Private Key authentication stored in GitHub Repository Secrets. Never hardcoded in repository files.
* **Execution Steps on Target Server**:
  1. Clones/pulls codebase into `/opt/auctionbot`.
  2. Builds production images via `Dockerfile.prod` and `docker-compose.prod.yml`.
  3. Launches back-end services (PostgreSQL, Redis) and application services (Web, Worker, Nginx).
  4. Runs `migrate` against the production database.
  5. Runs `collectstatic` to populate the shared static volume.
  6. Queries `/health/` to assert database and Redis availability.

---

## 4. Required GitHub Repository Secrets

Configure the following secrets in GitHub Repository Settings -> Secrets and variables -> Actions:

| Secret Name | Description | Example / Format |
|---|---|---|
| `SERVER_HOST` | Public IP or DNS of the deployment server | `187.77.94.137` |
| `SERVER_USER` | Deployment user with Docker permissions | `root` or `deploy` |
| `SSH_PRIVATE_KEY` | OpenSSH private key (RSA / Ed25519) | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `SERVER_PORT` | SSH port (optional, defaults to 22) | `22` |

> **Security Rule**: Secrets must exist solely in GitHub Encrypted Secrets or on the server itself. No secrets may ever be committed to git.
