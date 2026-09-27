# AuctionBot Architecture Specification

## Overview

AuctionBot is a unified, multi-tenant auction platform designed to consolidate multiple regional Telegram auction bots and marketplaces into a centralized, robust, asynchronous infrastructure.

The core architectural paradigm is:
```text
ONE CODEBASE + ONE DATABASE + MULTIPLE TENANTS
```

This document describes the architectural foundation established across **Phase 01 — Foundation** and **Phase 02 — Multi-Tenant Database Architecture**, clearly distinguishing implemented systems from future roadmap items.

---

## Architecture Implementation Status

| Component / Subsystem | Current Status | Implemented Phase | Description |
| :--- | :--- | :--- | :--- |
| **Project Foundation** | **Implemented** | Phase 01 | Django 5.2, modular settings hierarchy, structured logging |
| **Database Connection** | **Implemented** | Phase 01 | PostgreSQL integration with connection verification |
| **Redis Infrastructure** | **Implemented** | Phase 01 | Redis client, ping health check, and caching layer |
| **Celery Pipeline** | **Implemented** | Phase 01 | Celery app with Redis broker; `core.health_check_task` |
| **Health Endpoints** | **Implemented** | Phase 01 | `/health/`, `/health/live/`, `/health/ready/` active |
| **Docker Foundation** | **Implemented** | Phase 01 | `Dockerfile` & `docker-compose.yml` for local dev |
| **Tenant Database Model** | **Implemented** | Phase 02 | `Tenant` model with slug, code, timezone, currency |
| **Tenant Ownership Foundation** | **Implemented** | Phase 02 | `TenantOwnedModel` abstract base with `models.PROTECT` |
| **Tenant Query Pattern** | **Implemented** | Phase 02 | `TenantManager` with `.for_tenant(tenant)` helper |
| **Tenant Admin Interface** | **Implemented** | Phase 02 | Standard Django admin registration for Tenant entity |
| *Tenant Routing Middleware* | *Deferred* | Future Phase | Subdomain and header-based tenant resolution |
| *User / Tenant Membership* | *Deferred* | Future Phase | `TenantMembership` junction model and role permissions |
| *Telegram Engine / Bots* | *Deferred* | Phase 03 | Webhook router, command dispatcher, bot tokens |
| *Bidding Engine* | *Deferred* | Phase 04 | Anti-sniping, real-time bids, concurrency locks |
| *Wallet & Accounting* | *Deferred* | Phase 05 | Double-entry ledger, deposits, transactions |
| *Dashboard UI* | *Deferred* | Phase 06 | Multi-tenant administrative and analytics portal |
| *Legacy Data Migration* | *Deferred* | Phase 07 | Safe migration from `CYG_Aquatics_Malaysia` |

---

## Multi-Tenant Database Architecture (Phase 02)

### 1. Architectural Principle
The platform rejects multi-database sprawl (separate databases per country) in favor of a single unified PostgreSQL database where tenant boundaries are **explicitly represented at the relational model layer**.

```text
+-------------------------------------------------------------+
|                  Unified PostgreSQL Database                |
|                                                             |
|   +-----------------------------------------------------+   |
|   |                    Tenant (MY)                      |   |
|   |   name="CYG Malaysia", code="MY", slug="cyg"        |   |
|   +--------------------------+--------------------------+   |
|                              |                              |
|                              v Foreign Key (PROTECT)        |
|   +-----------------------------------------------------+   |
|   |            Tenant-Owned Records (MY)                |   |
|   |  Listings, Bids, Wallets, Telegram Config           |   |
|   +-----------------------------------------------------+   |
|                                                             |
|   +-----------------------------------------------------+   |
|   |                    Tenant (AU)                      |   |
|   |   name="AquaBid Australia", code="AU", slug="au"    |   |
|   +--------------------------+--------------------------+   |
|                              |                              |
|                              v Foreign Key (PROTECT)        |
|   +-----------------------------------------------------+   |
|   |            Tenant-Owned Records (AU)                |   |
|   |  Listings, Bids, Wallets, Telegram Config           |   |
|   +-----------------------------------------------------+   |
+-------------------------------------------------------------+
```

### 2. Tenant Model (`apps/tenants/models.py`)
The `Tenant` model represents an independent regional entity or marketplace organization:

- `name`: Human-readable display name (e.g., `'CYG Malaysia'`).
- `slug`: Unique, URL-safe and subdomain-compatible identifier (e.g., `'cyg'`). Immutable against display name cosmetic updates.
- `code`: Unique uppercase standard country/region code (e.g., `'MY'`, `'AU'`).
- `country`: Primary country of operations.
- `timezone`: Validated IANA timezone identifier (e.g., `'Asia/Kuala_Lumpur'`, `'Australia/Sydney'`). Enforced by `validate_iana_timezone` using Python's `zoneinfo`. Arbitrary human text is strictly rejected.
- `currency`: Standard 3-letter ISO 4217 currency code (e.g., `'MYR'`, `'AUD'`).
- `is_active`: Operational flag controlling whether the tenant is active.
- `created_at` / `updated_at`: Timezone-aware audit timestamps.

### 3. Tenant Identification Strategy
- **Primary Database Key**: `id` (`BigAutoField`).
- **External URL / Routing Key**: `slug`. Stored lowercase, indexed, unique. Used for subdomain routing (`<slug>.auctionbot.shop`).
- **Business / Regional Key**: `code`. Stored uppercase, indexed, unique. Used for ISO country mapping and internationalization.

Display name changes (e.g., "CYG Aquatics" -> "CYG Premium") do NOT change `slug` or `code`, preserving all external links, API routes, and relational integrity.

### 4. Mandatory Tenant Ownership Rule
> **Architectural Rule**: Every tenant-owned business record MUST maintain an explicit Foreign Key to `Tenant` at the database level.

The system will **never** rely on ambient state, request headers, Telegram bot tokens, or application memory alone as the database boundary. Database foreign keys guarantee referential integrity and prevent data orphaning.

### 5. Tenant-Owned Abstract Base Model (`TenantOwnedModel`)
All future tenant-specific models (Listings, Bids, Wallets, etc.) must subclass `TenantOwnedModel`:
```python
class SomeTenantEntity(TenantOwnedModel):
    # automatically inherits:
    # - tenant = ForeignKey("tenants.Tenant", on_delete=models.PROTECT, ...)
    # - created_at = DateTimeField(...)
    # - updated_at = DateTimeField(...)
    # - objects = TenantManager()
    ...
```

### 6. Deletion Behavior Strategy (`models.PROTECT`)
`TenantOwnedModel.tenant` is explicitly configured with `on_delete=models.PROTECT`.
- **Rationale**: Accidental cascading deletion (`models.CASCADE`) on a Tenant would permanently drop thousands of associated auctions, bids, and financial records.
- `models.PROTECT` strictly blocks deletion of a `Tenant` if any dependent business record exists, raising `django.db.models.deletion.ProtectedError`.

### 7. Query Pattern: Explicit Scoping
To prevent cross-tenant data leakage and avoid debugging nightmares:
- **No Global Thread-Local Magic**: The system deliberately avoids automatic global query filtering that alters `Model.objects.all()` behind the scenes.
- **Explicit Scoping**: Application code must explicitly scope queries:
  ```python
  # Standard explicit filter:
  records = Listing.objects.filter(tenant=current_tenant)

  # Custom manager helper:
  records = Listing.objects.for_tenant(current_tenant)
  ```

### 8. Database Constraints & Composite Uniqueness
- `Tenant.slug`: `UNIQUE`
- `Tenant.code`: `UNIQUE`
- `Tenant`: Index on `(is_active, code)`
- **Composite Uniqueness on Tenant-Owned Records**: Where external identifiers or codes exist (e.g. lot numbers, item codes, seller references), uniqueness is enforced as composite:
  ```python
  UniqueConstraint(fields=["tenant", "external_id"], name="...")
  ```
  This allows Tenant A and Tenant B to have overlapping item numbers (`LOT-01`) without collision.

### 9. Future Domain Relationships (Intentionally Deferred)

#### A. Users & Tenant Membership
Users are global entities in the unified platform. A single user can participate in multiple tenant marketplaces (e.g., buying in Malaysia and Australia).
- **Planned Junction Model**: `TenantMembership(user, tenant, role, is_active, created_at)`.
- *Status*: Explicitly deferred to user domain modeling.

#### B. Telegram Bot Configuration
Each tenant will maintain distinct Telegram bot configurations (seller bot, bidding bot, support chat, channel broadcasts).
- **Planned Model**: `TenantTelegramConfig(tenant, bot_token, bot_username, webhook_secret, ...)`.
- *Status*: Explicitly deferred to Phase 03 (Telegram Engine).

---

## Legacy System Boundary

The directory `E:\auctionbots\CYG_Aquatics_Malaysia` represents the legacy single-tenant deployment.

**Boundary Rule**:
- `CYG_Aquatics_Malaysia` is strictly a read-only reference.
- No files from the legacy project are imported, copied, or modified.
- No production cutover or database modification is performed during this phase.
