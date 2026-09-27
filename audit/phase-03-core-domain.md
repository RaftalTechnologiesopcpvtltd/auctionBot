# Phase 03 Audit — Core Domain Logic Extraction

## 1. Objective
The objective of Phase 03 is to extract, document, design, implement, and verify the core multi-tenant auction domain logic for AuctionBot (`E:\auctionbots\auctionBot`), replacing the single-tenant legacy implementation (`E:\auctionbots\CYG_Aquatics_Malaysia`) with a clean, decoupled, multi-tenant domain architecture.

The implementation establishes pure domain entities (`Listing`, `Auction`, `Bid`), explicit `Tenant` ownership via `TenantOwnedModel`, immutable financial records using `DecimalField`, typed state machines (`ListingStatus`, `AuctionStatus`), business domain services (`services/listings.py`, `services/auctions.py`, `services/bids.py`), and delete protection (`models.PROTECT`).

Telegram engine integration, concurrent locking/anti-sniping, Celery closing workers, and wallets/payments were strictly excluded and deferred to subsequent phases.

---

## 2. Legacy Source Inspection
Extensive read-only inspection was performed across the legacy project (`E:\auctionbots\CYG_Aquatics_Malaysia`):
- `home/models.py`: Discovered monolithic `AuctionItem` model combining listing details, auction timing, bidding state, Telegram chat IDs, and winner info. Discovered `Bidding` model storing bids. Discovered `Categories` model.
- `home/cron.py`: Discovered legacy closing logic, winner determination (`Bidding.objects.filter(item=...).order_by('-bid_amount').first()`), status changes (`is_live=False`, `is_sold=True/False`), and Telegram broadcast triggers.
- `telegram_bot/views.py`: Discovered Telegram webhook entry points, inline keyboard parsers, and command dispatches.
- `telegram_bot/fish_registration.py`: Discovered multi-step conversational state machine for listing submission (category, title, min price, bid step, buy now, media).
- `telegram_bot/show_bidding_list.py`: Discovered bid validation rules (`bid_amount >= current_bid + bid_step`), buy-now bypass, self-bidding prevention (`if user.id == item.seller_id`), and callback handling.
- `telegram_bot/payment.py`: Discovered ToyyibPay / payment webhook triggers.
- `report.md`: Documented architecture and pain points of the legacy single-tenant deployment.

Legacy baseline verification confirmed that **zero** workspace files in `E:\auctionbots\CYG_Aquatics_Malaysia` were modified.

---

## 3. Domain Discovery Summary
A dedicated discovery document was compiled at `docs/phase-03-domain-discovery.md` with 14 comprehensive sections detailing:
1. Legacy components inspected
2. Core business entities identified
3. Entity relationships
4. Auction lifecycle
5. Bid rules
6. Seller rules
7. Buyer rules
8. Product/Listing rules
9. Validation rules
10. Scheduling rules
11. Financial rules
12. Telegram responsibilities
13. Tenant differences (Malaysia vs Australia)
14. Legacy technical debt

---

## 4. Business Rules Extracted
The following business rules were extracted from the legacy inspection and codified into the new architecture:
1. **Separation of Listing and Auction**: A listing represents catalog item metadata and seller intent; an auction is a distinct temporal bidding event.
2. **Strict Monetary Typing**: All financial values (`start_price`, `bid_increment`, `buy_now_price`, `current_price`, `amount`) are represented strictly as `Decimal(12, 2)`. Float values are prohibited.
3. **Minimum Next Bid Formula**: If an auction has no previous bids, the minimum bid is `start_price`. If bids exist, the minimum next bid is `current_price + bid_increment`.
4. **Instant Buy-Now Option**: When `buy_now_price` is set and a bid meets or exceeds this threshold, the auction immediately transitions to `ENDED`, marks the bidder as `winner_id`, and closes further bidding.
5. **Anti-Shill Bidding**: Sellers cannot place bids on their own listings (`bidder_id != auction.listing.seller_id`).
6. **Active Time Window**: Bids are only accepted during an active auction window (`start_time <= now <= end_time`) and while `status == ACTIVE`.
7. **Winner Determination**: Upon auction conclusion, if bids exist, the highest valid bid amount determines `current_price` and the highest bidder becomes `winner_id`. If no bids exist, the auction concludes as unsold (`winner_id = None`).

---

## 5. New Domain Models

### `apps.listings.models.Listing`
- Subclasses: `TenantOwnedModel`
- Fields:
  - `tenant`: `ForeignKey('tenants.Tenant', on_delete=models.PROTECT)`
  - `seller_id`: `CharField(max_length=64, db_index=True)`
  - `title`: `CharField(max_length=255)`
  - `description`: `TextField(blank=True)`
  - `media_url`: `URLField(blank=True)`
  - `listing_type`: `CharField(choices=ListingType.choices, default=ListingType.AUCTION)`
  - `status`: `CharField(choices=ListingStatus.choices, default=ListingStatus.DRAFT)`
  - `total_quantity`: `PositiveIntegerField(default=1)`
  - `remaining_quantity`: `PositiveIntegerField(default=1)`
  - `created_at` / `updated_at`: `DateTimeField`

### `apps.bidding.models.Auction`
- Subclasses: `TenantOwnedModel`
- Fields:
  - `tenant`: `ForeignKey('tenants.Tenant', on_delete=models.PROTECT)`
  - `listing`: `OneToOneField('listings.Listing', on_delete=models.PROTECT, related_name='auction')`
  - `status`: `CharField(choices=AuctionStatus.choices, default=AuctionStatus.DRAFT)`
  - `start_time`: `DateTimeField(db_index=True)`
  - `end_time`: `DateTimeField(db_index=True)`
  - `start_price`: `DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])`
  - `bid_increment`: `DecimalField(max_digits=12, decimal_places=2, default=Decimal('1.00'), validators=[MinValueValidator(Decimal('0.01'))])`
  - `buy_now_price`: `DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)`
  - `current_price`: `DecimalField(max_digits=12, decimal_places=2)`
  - `winner_id`: `CharField(max_length=64, blank=True, null=True, db_index=True)`
  - `created_at` / `updated_at`: `DateTimeField`

### `apps.bidding.models.Bid`
- Subclasses: `TenantOwnedModel`
- Fields:
  - `tenant`: `ForeignKey('tenants.Tenant', on_delete=models.PROTECT)`
  - `auction`: `ForeignKey('bidding.Auction', on_delete=models.PROTECT, related_name='bids')`
  - `bidder_id`: `CharField(max_length=64, db_index=True)`
  - `amount`: `DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])`
  - `created_at`: `DateTimeField(auto_now_add=True, db_index=True)`

---

## 6. Relationships
- `Tenant` (1) ──< (N) `Listing` (`on_delete=models.PROTECT`)
- `Tenant` (1) ──< (N) `Auction` (`on_delete=models.PROTECT`)
- `Tenant` (1) ──< (N) `Bid` (`on_delete=models.PROTECT`)
- `Listing` (1) ── (1) `Auction` (`on_delete=models.PROTECT`)
- `Auction` (1) ──< (N) `Bid` (`on_delete=models.PROTECT`)

Cross-tenant linking is strictly blocked by model `clean()` validation:
- `auction.tenant_id == auction.listing.tenant_id`
- `bid.tenant_id == bid.auction.tenant_id`

---

## 7. Constraints
- **Referential Integrity**: Cascading deletes are prevented using `models.PROTECT`. Deleting a `Tenant`, `Listing`, or `Auction` with active children raises `ProtectedError`.
- **Time Ordering**: `auction.end_time > auction.start_time` enforced at model `clean()` and service creation.
- **Price Positivity**: `MinValueValidator(Decimal('0.01'))` on all monetary fields.
- **Buy-Now Validity**: If `buy_now_price` is specified, it must strictly exceed `start_price`.
- **Quantity Invariant**: `remaining_quantity <= total_quantity`.

---

## 8. Indexes
- `Listing`:
  - `Index(fields=['tenant', 'status'])`
  - `Index(fields=['tenant', 'seller_id'])`
- `Auction`:
  - `Index(fields=['tenant', 'status'])`
  - `Index(fields=['tenant', 'start_time', 'end_time'])`
- `Bid`:
  - `Index(fields=['tenant', 'auction', '-amount'])`

---

## 9. Domain Services
Encapsulated under `services/` without framework or UI coupling:
- `services.listings`:
  - `create_listing(...)`: Creates a listing in `DRAFT` or `PENDING_APPROVAL`.
  - `approve_listing(listing)`: Transitions status to `APPROVED`.
  - `reject_listing(listing, reason)`: Transitions status to `REJECTED`.
  - `close_listing(listing)`: Transitions status to `CLOSED`.
- `services.auctions`:
  - `create_auction(...)`: Validates approved listing, temporal window, and decimal prices. Creates auction in `DRAFT` or `SCHEDULED`.
  - `start_auction(auction)`: Transitions status to `ACTIVE`.
  - `close_auction(auction, now)`: Transitions to `ENDED`, determines highest bid and winner.
  - `cancel_auction(auction, reason)`: Transitions status to `CANCELLED`.
- `services.bids`:
  - `place_bid(auction, bidder_id, amount, now)`:
    - Enforces active auction status and valid time window.
    - Validates anti-shill bidding rule (`bidder_id != listing.seller_id`).
    - Validates minimum increment (`amount >= min_next_bid`).
    - Handles instant buy-now closure if `amount >= buy_now_price`.
    - Persists immutable `Bid` and updates `auction.current_price`.

---

## 10. State Transitions

### `ListingStatus`
- `DRAFT` → `PENDING_APPROVAL`
- `PENDING_APPROVAL` → `APPROVED`
- `PENDING_APPROVAL` → `REJECTED`
- `APPROVED` → `CLOSED`

### `AuctionStatus`
- `DRAFT` → `SCHEDULED`
- `SCHEDULED` → `ACTIVE`
- `ACTIVE` → `ENDED` (Sold or Unsold)
- `DRAFT` / `SCHEDULED` / `ACTIVE` → `CANCELLED`

---

## 11. Migrations
Applied cleanly to PostgreSQL:
- `apps/listings/migrations/0001_initial.py`: Created `Listing` table with indexes.
- `apps/bidding/migrations/0001_initial.py`: Created `Auction` and `Bid` tables with indexes.
- `apps/bidding/migrations/0002_initial.py`: Applied `Listing -> Auction` one-to-one relationship.
- `makemigrations --check`: Returns `No changes detected`.
- `migrate --plan`: Returns `No planned migration operations`.

---

## 12. Tests
Exact test execution results:
```text
Ran 39 tests in 1.649s
OK
```

Breakdown:
- `apps.core`: 11 tests (startup, configuration, PostgreSQL, Redis, Celery task eager and direct, health check endpoints)
- `apps.tenants`: 12 tests (tenant model, slugs, codes, timezone validation, isolation, composite uniqueness, protected deletion)
- `apps.listings`: 5 tests (listing creation, approval, rejection, quantity validation, cross-tenant isolation)
- `apps.bidding`: 11 tests (auction creation, temporal validation, mismatched tenant rejection, sequential bidding, increments, non-active rejection, buy-now auto-close, closing with winner, closing without bids, protected deletion, anti-shill bidding)

---

## 13. Regression Verification
All Phase 01 and Phase 02 test suites executed without regressions. System checks report 0 issues:
```text
System check identified no issues (0 silenced).
```

---

## 14. PostgreSQL Verification
Verified direct connection against PostgreSQL 18:
```text
PostgreSQL status: OK, result: (1,)
```
All tables (`tenants_tenant`, `listings_listing`, `bidding_auction`, `bidding_bid`) are created with appropriate primary keys, foreign keys, and indexes.

---

## 15. Redis Verification
Verified direct Redis connection against `127.0.0.1:6379`:
```text
Redis status: OK, ping: True
```

---

## 16. Celery Verification
Verified Celery task execution through Celery task pipeline:
```text
Celery task status: OK, result: {'status': 'ok', 'task': 'core.health_check_task', 'message': 'Celery worker pipeline is operational.'}
```

---

## 17. Health Endpoint Verification
Verified HTTP responses across all health check routes:
- `GET /health/`: `200 OK` → `{'status': 'ok', 'application': 'ok', 'database': 'ok', 'redis': 'ok'}`
- `GET /health/live/`: `200 OK` → `{'status': 'alive'}`
- `GET /health/ready/`: `200 OK` → `{'status': 'ready', 'details': {'database': 'ready', 'redis': 'ready'}}`

---

## 18. Legacy Project Verification
The legacy directory `E:\auctionbots\CYG_Aquatics_Malaysia` was checked for file modifications:
- Number of files modified during Phase 03: **0 files**
- Legacy codebase remains strictly read-only and unchanged.

---

## 19. Security Review
- **Secrets Scan**: Verified no tokens, passwords, keys, or `.env` files are tracked in git.
- **Tenant Isolation**: Model validation and query managers strictly isolate tenant data.
- **Delete Protection**: `models.PROTECT` prevents accidental cascading data loss.

---

## 20. Known Limitations
- Concurrency locks (Redis mutexes) are not yet active on bid placement; simultaneous bids at the exact same millisecond will be handled in Phase 05.
- Anti-sniping (dynamic auction extension when a bid arrives in the final N minutes) is not yet implemented.
- Asynchronous auction closing workers are not yet running in Celery beat.

---

## 21. Deferred Work
- **Phase 04 — Telegram Engine**: Webhook routers, command handlers, conversational FSMs, bot configuration.
- **Phase 05 — Bidding Engine & Concurrency**: Redis distributed locking, atomic Lua bid placement, anti-sniping dynamic window extension, Celery closing worker.
- **Phase 06 — Wallets & Accounting**: Ledger entries, security deposits, gateway integrations, commissions.
- **Phase 07 — Dashboard UI**: Multi-tenant web management portal.
- **Phase 08 — Legacy Data Migration**: Safe migration of historic listings and user profiles from `CYG_Aquatics_Malaysia`.

---

## 22. Rollback Plan
If rollback of Phase 03 is necessary:
1. Revert Git commit: `git revert <phase-03-commit>`
2. Roll back database migrations:
   ```bash
   python manage.py migrate bidding zero
   python manage.py migrate listings zero
   ```
3. Remove apps from `INSTALLED_APPS` in `config/settings/base.py`.

---

## 23. Next Phase
**Phase 04 — Telegram Engine**:
Establish the multi-tenant Telegram bot infrastructure, webhook routing, bot dispatching, and conversational handlers for sellers and buyers.

---

## 24. Final Status
**PASS**: Core domain logic extraction is complete, fully tested, documented, and verified.
