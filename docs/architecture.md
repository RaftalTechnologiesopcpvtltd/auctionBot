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
| **Core Domain Models** | **Implemented** | Phase 03 | `Listing`, `Auction`, `Bid` with strict Decimal monetary fields |
| **Domain Services Layer** | **Implemented** | Phase 03 | `services/listings.py`, `services/auctions.py`, `services/bids.py` |
| **Domain Lifecycle State Machines** | **Implemented** | Phase 03 | `ListingStatus`, `AuctionStatus` typed TextChoices transitions |
| **Telegram Engine Foundation** | **Implemented** | Phase 04 | Webhook router, tenant resolution, secret tokens, dispatcher |
| **Telegram Bot Configuration** | **Implemented** | Phase 04 | `TelegramBotConfig` with Fernet-encrypted bot tokens at rest |
| **Telegram User Identity & Idempotency** | **Implemented** | Phase 04 | `TelegramUser` (tenant-scoped) & `TelegramUpdateLog` deduplication |
| **Telegram Service Abstraction** | **Implemented** | Phase 04 | `TelegramService(tenant)` with mockable HTTP client |
| **Concurrent Bidding Engine** | **Implemented** | Phase 05 | PostgreSQL row locks (`select_for_update`), anti-sniping, idempotency |
| **Asynchronous Auction Closing** | **Implemented** | Phase 05 | Celery task `bidding.close_auction_task` with idempotent finalization |
| *Tenant Routing Middleware* | *Deferred* | Future Phase | Subdomain and header-based tenant resolution |
| *User / Tenant Membership* | *Deferred* | Future Phase | `TenantMembership` junction model and role permissions |
| *Wallet & Accounting* | *Deferred* | Phase 06 | Double-entry ledger, deposits, commissions, refunds |
| *Dashboard UI* | *Deferred* | Phase 07 | Multi-tenant administrative and analytics portal |
| *Legacy Data Migration* | *Deferred* | Phase 08 | Safe migration from `CYG_Aquatics_Malaysia` |

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
- *Status*: Explicitly deferred to Phase 04 (Telegram Engine).

---

## Core Domain Architecture (Phase 03)

### 1. Architectural Principles
1. **Explicit Tenant Ownership**: Every domain entity (`Listing`, `Auction`, `Bid`) directly inherits `TenantOwnedModel` with `on_delete=models.PROTECT`. No cross-tenant mixing is permissible.
2. **Framework Separation**: Domain rules are encapsulated in pure Python/Django business services (`services/listings.py`, `services/auctions.py`, `services/bids.py`). Views, serializers, and Telegram handlers are consumers of these services, not domain logic containers.
3. **Monetary Rigor**: All currency fields (`start_price`, `bid_increment`, `buy_now_price`, `current_price`, `amount`) are strictly defined as `DecimalField(max_digits=12, decimal_places=2)`. Floating point representation is strictly prohibited.
4. **Referential Deletion Protection**: Cascades from listing to auction or auction to bid are blocked with `models.PROTECT` to protect financial auditability.

```text
+--------------------------------------------------------------+
|                          Tenant                              |
+------------------------------+-------------------------------+
                               |
            +------------------+------------------+
            | PROTECT                             | PROTECT
            v                                     v
+-----------------------+             +-----------------------+
|        Listing        | <---------- |        Auction        |
| - seller_id           |   PROTECT   | - status (DRAFT->...) |
| - title, description  |             | - start/end times     |
| - listing_type        |             | - start/increment/curr|
| - status              |             | - winner_id           |
+-----------------------+             +-----------+-----------+
                                                  |
                                                  | PROTECT
                                                  v
                                      +-----------------------+
                                      |          Bid          |
                                      | - bidder_id           |
                                      | - amount              |
                                      | - created_at          |
                                      +-----------------------+
```

### 2. Core Entities

#### Listing (`apps.listings.models.Listing`)
Represents an item or lot submitted for auction or fixed-price purchase.
- `tenant`: Foreign key to `Tenant` (`PROTECT`).
- `seller_id`: Generic identifier for seller (supporting future User FK or external actor ID).
- `title`, `description`, `media_url`: Catalog data.
- `listing_type`: TextChoices (`AUCTION`, `BUY_NOW`, `HYBRID`).
- `status`: TextChoices (`DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `CLOSED`).
- `created_at` / `updated_at`: Audit timestamps.

#### Auction (`apps.bidding.models.Auction`)
Represents a scheduled, live, or concluded bidding event for a specific listing.
- `tenant`: Foreign key to `Tenant` (`PROTECT`).
- `listing`: One-to-one foreign key to `Listing` (`PROTECT`). Listing and Auction tenant IDs must match.
- `start_time` / `end_time`: Timezone-aware datetimes defining the active bidding window.
- `start_price`: Initial floor price (`DecimalField`).
- `bid_increment`: Minimum increment per subsequent bid (`DecimalField`).
- `buy_now_price`: Optional instant purchase threshold (`DecimalField`).
- `current_price`: Live highest accepted price (`DecimalField`).
- `status`: TextChoices (`DRAFT`, `SCHEDULED`, `ACTIVE`, `ENDED`, `CANCELLED`).
- `winner_id`: Buyer identifier who won the auction upon ending.

#### Bid (`apps.bidding.models.Bid`)
Represents an immutable financial bid placed by a bidder.
- `tenant`: Foreign key to `Tenant` (`PROTECT`).
- `auction`: Foreign key to `Auction` (`PROTECT`). Auction and Bid tenant IDs must match.
- `bidder_id`: Generic identifier for the bidder.
- `amount`: Bid amount (`DecimalField`).
- `created_at`: Exact timestamp when the bid was registered.

### 3. Business Service Layer

- **`services.listings`**:
  - `create_listing(...)`: Validates and creates listings in `DRAFT` or `PENDING_APPROVAL`.
  - `approve_listing(listing)`: Transitions `PENDING_APPROVAL` -> `APPROVED`.
  - `reject_listing(listing, reason)`: Transitions `PENDING_APPROVAL` -> `REJECTED`.
  - `close_listing(listing)`: Transitions `APPROVED` -> `CLOSED`.

- **`services.auctions`**:
  - `create_auction(...)`: Validates listing state (must be `APPROVED`), start/end times (`start_time < end_time`), decimal prices, and creates auction in `DRAFT` or `SCHEDULED`.
  - `start_auction(auction)`: Validates transition to `ACTIVE`.
  - `close_auction(auction, now)`: Transitions `ACTIVE` -> `ENDED`, evaluates highest bid, determines `winner_id` and final `current_price`.
  - `cancel_auction(auction, reason)`: Transitions `DRAFT`/`SCHEDULED`/`ACTIVE` -> `CANCELLED`.

- **`services.bids`**:
  - `place_bid(auction, bidder_id, amount, now)`:
    - Verifies auction is in `ACTIVE` status.
    - Validates active time window (`auction.start_time <= now <= auction.end_time`).
    - Enforces tenant isolation between bidder, auction, and listing.
    - **Anti-Shill Protection**: Strictly rejects bids if `bidder_id == auction.listing.seller_id`.
    - Enforces minimum next bid (`amount >= current_price + bid_increment` or `start_price`).
    - Triggers instant buy-now conclusion if `amount >= buy_now_price`.
    - Creates immutable `Bid` record and updates `auction.current_price`.

### 4. Integrity Constraints & Database Indexes
- **Tenant Scope Indexing**:
  - `Listing`: indexes on `(tenant, status)`, `(tenant, seller_id)`.
  - `Auction`: indexes on `(tenant, status)`, `(tenant, start_time, end_time)`.
  - `Bid`: indexes on `(tenant, auction, -amount)`.
- **Validation Constraints**: Model `clean()` enforces tenant identity consistency across `Listing <-> Auction <-> Bid`.

### 5. Boundaries and Deferred Capabilities
The following items are intentionally excluded from Phase 03 to maintain clean boundaries:
1. **Concurrent Bidding Engine & Distributed Locks**: High-concurrency Redis mutexes, atomic Lua scripts, and anti-sniping dynamic extensions are deferred to Phase 05.
2. **Asynchronous Auction Closing Workers**: Celery beat schedules and polling workers for auto-closing expired auctions are deferred to Phase 05.
3. **Wallets & Financial Settlement**: Double-entry ledger, deposits, payment gateway webhooks, and seller disbursements are deferred to Phase 06.

---

## Multi-Tenant Telegram Engine (Phase 04)

### 1. Ingestion Pipeline & Architectural Flow
The Telegram Engine decouples incoming messaging updates from core domain models using a strictly layered processing pipeline:

```text
Telegram Update (HTTPS POST)
            ↓
  Tenant Resolution (/telegram/webhook/<tenant_slug>/<bot_type>/)
            ↓
  Webhook Secret Verification (X-Telegram-Bot-Api-Secret-Token)
            ↓
  Idempotency Deduplication (TelegramUpdateLog)
            ↓
  Update Dispatcher (TelegramDispatcher)
            ↓
  User Profile Synchronization (TelegramUser)
            ↓
  Command & Callback Routers (/start, /help, callback_query)
            ↓
  Application Services (Decoupled Orchestration)
            ↓
  Domain Services (services/listings.py, services/bids.py)
            ↓
  PostgreSQL Relational Layer
```

### 2. Tenant Resolution
- Webhook endpoints explicitly encode tenant routing:
  - Default Unified: `POST /telegram/webhook/<slug:tenant_slug>/`
  - Typed Bot: `POST /telegram/webhook/<slug:tenant_slug>/<str:bot_type>/`
- Routing deterministically maps to an active `Tenant` record. User-supplied parameters or client-side country selectors are never trusted as security boundaries.

### 3. Telegram Configuration & Token Security (`TelegramBotConfig`)
- Subclasses `TenantOwnedModel` with `on_delete=models.PROTECT`.
- **Encrypted Token Storage**: Tokens are encrypted at rest using standard `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256) keyed by SHA-256 derived from `settings.SECRET_KEY` or `TELEGRAM_TOKEN_ENCRYPTION_KEY`.
- **Environment Override**: Supports 12-factor cloud deployments via `token_env_var` (e.g. `CYG_TELEGRAM_BOT_TOKEN`), overriding database ciphertext.
- **Admin Masking**: Tokens are never displayed in plaintext or returned in unmasked serializers (`masked_token` returns `1234...9876`).

### 4. Webhook Security Foundation
- **Secret Token Header**: Evaluates `X-Telegram-Bot-Api-Secret-Token` matching the tenant's `webhook_secret_token`. Mismatches immediately return `403 Forbidden`.
- **Strict Method Enforcement**: Rejects non-POST requests with `405 Method Not Allowed`.
- **Sanitized Errors**: Does not leak stack traces or internal configuration to external callers.

### 5. Idempotency & Deduplication (`TelegramUpdateLog`)
- Telegram webhooks may retry delivery during network blips or timeouts.
- `TelegramUpdateLog` enforces a composite unique constraint on `(tenant, update_id)`.
- If an update has already been registered for the tenant, the endpoint returns `{"status": "duplicate"}` with HTTP 200 immediately, suppressing duplicate commands, bids, or notifications.

### 6. Tenant-Scoped User Identity (`TelegramUser`)
- Telegram users are identified externally by stable 64-bit integer `telegram_user_id`.
- Identity is strictly tenant-scoped via composite unique constraint: `(tenant, telegram_user_id)`.
- A user interacting with CYG Malaysia (`Tenant MY`) and AquaBid Australia (`Tenant AU`) maintains separate preferences, roles, and profiles for each regional marketplace.

### 7. Telegram Service Abstraction (`services.telegram.TelegramService`)
- Encapsulates low-level HTTP Bot API communication:
  - `send_message(chat_id, text, parse_mode, reply_markup)`
  - `edit_message_text(chat_id, message_id, text, parse_mode, reply_markup)`
  - `answer_callback_query(callback_query_id, text, show_alert)`
- Bound to a tenant's bot configuration (`TelegramService(tenant)`).
- Mockable HTTP adapter ensures zero live network requests occur during automated test execution.

### 8. Boundaries and Deferred Capabilities
The following capabilities are deferred to future phases:
1. **Long Polling & Background Daemons**: Strictly rejected in favor of webhooks. No long-running polling processes.
2. **Conversational FSMs & Seller Registration**: Multi-step state machines for listing registration are deferred to subsequent feature phases.
3. **Production Webhook Registration**: Registration of public domains via `setWebhook` is deferred to deployment phases.

---

## Concurrent Bidding Engine (Phase 05)

### 1. Concurrency Architecture & PostgreSQL Row Locking
PostgreSQL is the **authoritative source of truth** for all auction states, bids, and timing data.
- **Pessimistic Row Locking**: When a bid is submitted or an auction is finalized, the engine executes:
  ```python
  Auction.objects.select_for_update().select_related("listing", "tenant").get(id=auction_id)
  ```
- **Serialization Guarantee**: Any competing thread or process attempting to bid on or close the same auction is blocked until the active database transaction commits.
- **Authoritative In-Flight Recalculation**: Once the row lock is acquired, the engine reloads the current database state. Price increments and validity checks are calculated strictly from the locked row, preventing lost updates and dirty reads.

```text
Concurrent Bid A (Worker 1) ──┐
                              ├─► BEGIN TRANSACTION ──► SELECT FOR UPDATE (Auction #X)
Concurrent Bid B (Worker 2) ──┘        │
                                       ▼ (Worker 1 acquires lock; Worker 2 blocks)
                              1. Validate state, timing, bidder eligibility
                              2. Recalculate min_next_bid from locked price
                              3. Create Bid row (with idempotency_key)
                              4. Update Auction current_price, highest_bid
                              5. Apply anti-sniping extension if near end_at
                              6. COMMIT TRANSACTION
                                       │
                                       ▼ (Worker 2 acquires lock, re-reads state)
                              Evaluates newly committed price -> Rejects or accepts
```

### 2. Lock Ordering & Deadlock Prevention
- All operations involving an auction and its bids follow a strict, single-tier lock hierarchy:
  1. Lock `Auction` row first.
  2. Perform secondary inserts/updates (`Bid`, `Listing`).
- Circular lock acquisitions are prohibited across all domain services.

### 3. Anti-Sniping Dynamic Extension
- **Threshold Window**: Configured per auction (`anti_sniping_seconds`, default 120s).
- **Extension Duration**: Configured per auction (`extension_seconds`, default 120s).
- **Atomic Extension**: If a valid bid arrives within the threshold window, `auction.end_at` is extended by `extension_seconds` and `extension_count` is incremented under the row lock.
- **Concurrent Observer Guarantee**: Any concurrent worker attempting to close the auction immediately observes the extended `end_at` upon lock acquisition and aborts closing.

### 4. Bid Idempotency
- `Bid` maintains an optional `idempotency_key` with a composite unique constraint: `(tenant, auction, idempotency_key)`.
- If a client or webhook retries a bid with an existing key, the service immediately returns the existing `Bid` and `Auction` records without creating duplicate records or advancing the price ladder.

### 5. Asynchronous Auction Closing (`bidding.close_auction_task`)
- Executed via Celery with automatic retry handling (`max_retries=3`, `default_retry_delay=5`).
- **Idempotency**: If the auction is already `SOLD`, `UNSOLD`, or `CANCELLED`, the task returns cleanly without duplicate operations.
- **Timing Invariant**: The task asserts that current time has reached or passed `end_at`. If an auction was extended by anti-sniping, premature closure is blocked.
- **Outcome Resolution**: Highest bid sets `status=SOLD` and `winner_id`; zero bids sets `status=UNSOLD`.

### 6. Role of Redis
- **Secondary Infrastructure Role**: Redis is utilized exclusively as the Celery task broker and caching layer.
- **No Redis Locks**: Distributed Redis mutexes are deliberately omitted because PostgreSQL's row-level locking natively provides ACID transactional serialization with zero risk of cache/database divergence or split-brain states.

### 7. External Side-Effect Boundary
- External network requests (e.g. Telegram API `sendMessage`, payment webhooks) are **strictly forbidden** inside the database transaction block. All outbound side-effects must occur after the transaction has committed.

---

## Legacy System Boundary

The directory `E:\auctionbots\CYG_Aquatics_Malaysia` represents the legacy single-tenant deployment.

**Boundary Rule**:
- `CYG_Aquatics_Malaysia` is strictly a read-only reference.
- No files from the legacy project are imported, copied, or modified.
- No production cutover or database modification is performed during this phase.

