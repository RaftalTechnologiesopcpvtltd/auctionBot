# AuctionBot Architecture Specification

## Overview

AuctionBot is a unified, multi-tenant auction platform designed to consolidate multiple regional Telegram auction bots and marketplaces into a centralized, robust, asynchronous infrastructure.

This document describes the architectural foundation established in **Phase 01 — Foundation** and outlines the distinction between what is currently operational and what is slated for future phases.

---

## Phase 01 — Implemented Architecture

Phase 01 establishes the foundational infrastructure stack required for scalable, distributed operation.

### Component Diagram

```
                 +-----------------------+
                 |    HTTP Clients /     |
                 |    Health Monitors    |
                 +-----------+-----------+
                             |
                             v
                +-------------------------+
                |     Django Application   |
                |   (WSGI / ASGI / HTTP)   |
                +----+---------------+----+
                     |               |
        Database SQL |               | Cache / Broker
                     v               v
            +------------+     +-----------+
            | PostgreSQL |     |   Redis   |
            +------------+     +-----+-----+
                                     |
                         Celery Task | Queue
                                     v
                           +-------------------+
                           |   Celery Worker   |
                           |  (Async Runtime)  |
                           +-------------------+
```

### Operational Subsystems

1. **Django Web Application Layer (`apps/core`, `config/`)**:
   - Structured settings split across `base.py`, `development.py`, and `production.py`.
   - Strict 12-factor configuration driven by environment variables (`.env`).
   - Clean application modularization under `apps/`.
   - Health monitoring endpoints:
     - `/health/`: Comprehensive dependency health probe (Database + Redis).
     - `/health/live/`: High-frequency liveness probe.
     - `/health/ready/`: Backing-service readiness probe.

2. **Persistence Layer (PostgreSQL)**:
   - Primary relational database (`auctionbot_db`).
   - Native connection handling via `psycopg2-binary` / `psycopg3`.
   - Django core migrations applied and operational.

3. **Message Broker & Coordination Layer (Redis)**:
   - In-memory key-value store serving as the Celery message broker and application cache.
   - Low-latency queuing for asynchronous task dispatch.

4. **Background Task Execution (Celery)**:
   - Distributed task pipeline defined in `config/celery.py`.
   - Task autodiscovery across Django applications.
   - Verification task (`core.health_check_task`) deployed to validate the end-to-end task queue lifecycle (`Django -> Celery -> Redis -> Worker`).

---

## Clear Separation: Implemented vs. Future

| Component / Subsystem | Phase 01 Status | Target Phase | Description |
| :--- | :--- | :--- | :--- |
| **Project Foundation** | **Implemented** | Phase 01 | Django 5.2, settings hierarchy, logging, `.env.example` |
| **Database Connection** | **Implemented** | Phase 01 | PostgreSQL verified, migrations applied |
| **Redis Infrastructure** | **Implemented** | Phase 01 | Redis ping and cache verified |
| **Celery Pipeline** | **Implemented** | Phase 01 | Celery app configured, test task executes |
| **Health Endpoints** | **Implemented** | Phase 01 | `/health/`, `/health/live/`, `/health/ready/` active |
| **Docker Foundation** | **Implemented** | Phase 01 | `Dockerfile` & `docker-compose.yml` for local dev |
| **Automated Tests** | **Implemented** | Phase 01 | 12 automated unit & integration tests passing |
| *Multi-Tenant Data Model* | *Not Implemented* | Phase 02 | Tenant model, schema/schema-less tenant isolation |
| *Tenant Routing* | *Not Implemented* | Phase 02 | Domain / header / slug based tenant resolution |
| *Telegram Engine / Bots* | *Not Implemented* | Phase 03 | Webhook router, command dispatcher, session manager |
| *Bidding Engine* | *Not Implemented* | Phase 04 | Anti-sniping, real-time bids, concurrency locks |
| *Wallet & Accounting* | *Not Implemented* | Phase 05 | Double-entry ledger, deposits, transactions |
| *Admin Dashboard* | *Not Implemented* | Phase 06 | Management portal for tenants and listings |
| *Legacy Migration* | *Not Implemented* | Phase 07 | Safe migration from `CYG_Aquatics_Malaysia` |

---

## Directory Organization

```text
auctionBot/
│
├── config/
│   ├── __init__.py           # Exports celery_app
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py           # Shared settings, logging, middleware
│   │   ├── development.py    # Local dev overrides
│   │   └── production.py     # Hardened security & strict env checks
│   ├── urls.py               # Root routing
│   ├── asgi.py               # ASGI gateway
│   ├── wsgi.py               # WSGI gateway
│   └── celery.py             # Celery instance configuration
│
├── apps/
│   ├── core/                 # Health checks, liveness probes, core tasks
│   ├── tenants/              # Reserved for multi-tenant models (Phase 02)
│   ├── users/                # Reserved for user domain
│   ├── listings/             # Reserved for auction catalog
│   ├── bidding/              # Reserved for bidding engine
│   ├── wallets/              # Reserved for balance and ledger
│   ├── telegram_engine/      # Reserved for Telegram bot routing
│   └── dashboard/            # Reserved for administrative UI
│
├── services/                 # Shared domain logic
├── tasks/                    # Project-level Celery tasks
├── tests/                    # Top-level test suite
├── scripts/                  # Operational scripts
├── audit/                    # Phase verification audits
├── docs/                     # Architectural specifications
├── requirements/             # Layered requirement definitions
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
│
├── .env.example              # Environment variables template
├── .gitignore                # Exclusion rules for secrets, venv, media
├── docker-compose.yml        # Development multi-container definition
├── Dockerfile                # Web & worker container image definition
├── manage.py                 # Administrative CLI
└── README.md                 # Primary project documentation
```

---

## Legacy System Boundary

The directory `E:\auctionbots\CYG_Aquatics_Malaysia` represents the legacy single-tenant deployment.

**Boundary Rule**:
- `CYG_Aquatics_Malaysia` is strictly a read-only reference.
- No files from the legacy project are imported, copied, or modified in Phase 01.
- No production cutover or database modification is performed during this phase.
