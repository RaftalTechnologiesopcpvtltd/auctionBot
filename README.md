# AuctionBot

Unified foundation for the future multi-country Telegram auction and marketplace platform.

---

## Current Status

* **Current Phase**: `Phase 01 — Foundation`
* **Status**: Complete & Verified
* **Target Python Version**: Python 3.11+
* **Framework**: Django 5.2+
* **Task Pipeline**: Celery 5.4+ with Redis broker
* **Database**: PostgreSQL 16+

### What is Implemented in Phase 01
- [x] Clean, modular project structure with separated environment settings (`base.py`, `development.py`, `production.py`).
- [x] PostgreSQL database configuration with connectivity verification and migrations applied.
- [x] Redis connection and caching integration.
- [x] Celery application configuration and verified end-to-end task execution pipeline (`Django -> Celery -> Redis -> Worker`).
- [x] Health monitoring endpoints (`/health/`, `/health/live/`, `/health/ready/`).
- [x] Structured, non-polluting logging configuration.
- [x] Containerization assets (`Dockerfile`, `docker-compose.yml`).
- [x] Automated test suite covering settings, database, Redis, Celery, and health checks.
- [x] Phase 01 architecture documentation and comprehensive audit log.

### What is Explicitly Deferred to Future Phases
* Multi-tenant database logic and Tenant models (Phase 02)
* Telegram webhooks, polling, and bot engines (Phase 03)
* Bidding engine, auto-bidding, and anti-sniping logic (Phase 04)
* Wallet, payments, and accounting ledger (Phase 05)
* Administrative dashboards and metrics (Phase 06)
* Data migration from legacy systems (Phase 07)

---

## IMPORTANT: Legacy Reference Notice

> [!WARNING]
> The directory `E:\auctionbots\CYG_Aquatics_Malaysia` represents an older, legacy production project.
> It is retained strictly as a **READ-ONLY REFERENCE**.
> It must never be edited, moved, committed, or mutated by AuctionBot workflows.

---

## Local Setup & Development

### 1. Prerequisites
- Python 3.11 or higher
- PostgreSQL running locally (port 5432) or via Docker
- Redis running locally (port 6379) or via Docker
- Git

### 2. Clone & Environment Setup
```bash
git clone https://github.com/RaftalTechnologiesopcpvtltd/auctionBot.git
cd auctionBot

# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements/development.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` and configure appropriate credentials:
```bash
cp .env.example .env
```
Key variables:
- `DJANGO_SECRET_KEY`: Secret cryptographic key
- `POSTGRES_DB`: Name of the PostgreSQL database (e.g. `auctionbot_db`)
- `POSTGRES_USER`: PostgreSQL user
- `POSTGRES_PASSWORD`: PostgreSQL password
- `POSTGRES_HOST`: PostgreSQL host (e.g. `127.0.0.1`)
- `POSTGRES_PORT`: 5432
- `REDIS_URL`: Redis URI (e.g. `redis://127.0.0.1:6379/0`)
- `CELERY_BROKER_URL`: Celery broker URI

### 4. Database Initialization & Migrations
```bash
python manage.py migrate
```

### 5. Running the Application
```bash
python manage.py runserver
```
Visit http://127.0.0.1:8000/health/ to verify the application status.

### 6. Running Celery Worker
```bash
# On Windows:
celery -A config worker --pool=solo --loglevel=INFO

# On Linux/macOS:
celery -A config worker --loglevel=INFO
```

### 7. Running Automated Tests
```bash
python manage.py test
```

---

## Docker Quickstart

To run the entire development stack (Web + PostgreSQL + Redis + Worker) using Docker:

```bash
docker compose up --build
```
The web server will be available at http://127.0.0.1:8000.

---

## Health Check Endpoints

- `GET /health/`: Returns full diagnostic status of Django, PostgreSQL, and Redis.
  ```json
  {
    "status": "ok",
    "application": "ok",
    "database": "ok",
    "redis": "ok"
  }
  ```
- `GET /health/live/`: Lightweight liveness probe returning `{"status": "alive"}` (HTTP 200).
- `GET /health/ready/`: Readiness probe checking backing dependencies (HTTP 200 when ready, HTTP 503 when not ready).

---

## License

Proprietary — Raftal Technologies OPC Pvt Ltd.
