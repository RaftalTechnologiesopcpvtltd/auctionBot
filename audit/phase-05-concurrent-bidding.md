# Phase 05 Audit — Concurrent Bidding Engine

## 1. Objective
The objective of Phase 05 is to design, implement, and rigorously verify a production-grade, concurrency-safe bidding engine for AuctionBot (`E:\auctionbots\auctionBot`). The engine ensures that concurrent bid placements, rapid outbid competitions, dynamic anti-sniping extensions, and asynchronous auction closures execute with full ACID guarantees against PostgreSQL without race conditions, dirty reads, or data corruption.

---

## 2. Legacy Bidding Discovery
Read-only inspection of `E:\auctionbots\CYG_Aquatics_Malaysia` revealed:
- `show_bidding_list.py`: Handled bids via non-atomic integer addition (`int(cat_price) + amount`), with no database transactions (`transaction.atomic`) or row-level locking (`select_for_update`).
- Race condition vulnerability: Concurrent bids on the same listing read the identical `auction_current_amount`, resulting in silent overwrites and corrupted price ladders.
- Anti-sniping: Zero anti-sniping or extension logic existed in legacy code.
- Closing: Implemented via an unmanaged polling loop in `home/cron.py`, causing duplicate winner alerts if the process crashed.
- Complete findings documented in `docs/phase-05-bidding-discovery.md`.

---

## 3. Business Rules
1. **Minimum Valid Bid**: First bid must be `>= starting_price`; subsequent bids must be `>= current_price + bid_increment`.
2. **Instant Buy-Now**: If `buy_now_price` is configured and a bid meets or exceeds it, the auction is immediately awarded to the buyer, marked `SOLD`, and closed to further bidding.
3. **Anti-Shill Bidding**: Sellers cannot bid on their own listings (`bidder_id != auction.listing.seller_id`).
4. **Active Window Enforcement**: Bids are only valid when `status == ACTIVE` and `start_at <= now <= end_at`.
5. **Anti-Sniping Dynamic Extension**: If a valid bid arrives within the threshold window (`anti_sniping_seconds`, default 120s), `end_at` is extended by `extension_seconds` (default 120s).
6. **Winner Determination**: Upon closing, highest bid designates `winner_id` and final `winning_price`; auctions with zero bids close as `UNSOLD`.

---

## 4. Concurrency Model
- **PostgreSQL Row-Level Locking (`SELECT FOR UPDATE`)**: Every bid and auction closing operation begins by acquiring an exclusive row lock on the target `Auction` row inside a `transaction.atomic()` block.
- **Serialization**: Competing transactions for the same auction are queued and executed strictly serially by the PostgreSQL lock manager.
- **Authoritative Recalculation**: Stale in-memory variables are discarded. The locked database row provides the authoritative state for price increments and status checks.

---

## 5. PostgreSQL Locking
- Lock statement: `Auction.objects.select_for_update().select_related("listing", "tenant").get(id=auction_id)`
- Row-level lock scope: Locks only the specific `Auction` row being modified; other auctions across the same or different tenants proceed in parallel without contention.
- Tested and verified under true multi-threaded concurrency using `TransactionTestCase` against PostgreSQL.

---

## 6. Redis Role
- **Secondary Infrastructure Role**: Redis is utilized exclusively as the Celery message broker and caching layer.
- **No Redis Locks**: Distributed Redis mutexes were evaluated and intentionally omitted because PostgreSQL's native row-level locking provides transactional ACID guarantees directly on the authoritative database with zero risk of cache/DB divergence or split-brain states.

---

## 7. Transaction Boundaries
- Database transactions encompass database reads, row lock acquisition, state validation, `Bid` row insertion, and `Auction` field updates only.
- External operations (Telegram Bot API calls, payment webhooks, external HTTP requests) are strictly forbidden inside the database transaction and execute only after commit.

---

## 8. Idempotency
- `Bid` model includes `idempotency_key = CharField(max_length=64, blank=True, null=True, db_index=True)`.
- Database constraint: `UniqueConstraint(fields=['tenant', 'auction', 'idempotency_key'], condition=Q(idempotency_key__isnull=False))`.
- When an identical idempotency key is resubmitted, `place_bid` returns the existing `Bid` and `Auction` without creating duplicate records or advancing the price.

---

## 9. Anti-Sniping
- Fields on `Auction`: `anti_sniping_seconds` (default 120), `extension_seconds` (default 120), `extension_count` (default 0).
- When a bid arrives with `(end_at - now).total_seconds() <= anti_sniping_seconds`:
  - `end_at` is atomically extended by `extension_seconds`.
  - `extension_count` is incremented.
- Verified in `test_concurrent_bids_near_end_trigger_anti_sniping`.

---

## 10. Auction Closing
Implemented in `services.auctions.close_auction`:
- Locks the `Auction` row using `select_for_update()`.
- Idempotency guard: If auction status is already `SOLD`, `UNSOLD`, `CLOSED`, or `CANCELLED`, returns immediately.
- Timing invariant: Asserts that current time has reached or passed `end_at`. If anti-sniping extended the auction, premature closure is blocked with `ValidationError`.
- Resolves leading bid (`auction.bids.order_by("-amount", "-placed_at").first()`) to designate winner and price.

---

## 11. Celery Tasks
Implemented in `apps.bidding.tasks.close_auction_task`:
- Named task: `bidding.close_auction_task`.
- Decorated with `@shared_task(bind=True, max_retries=3, default_retry_delay=5)`.
- Safe for repeated execution; verified in `test_celery_close_auction_task_execution_and_idempotency`.

---

## 12. Error Handling
Domain-specific exceptions inheriting from `ValidationError` (guaranteeing backwards compatibility):
- `BiddingError`
- `AuctionNotActiveError`
- `AuctionClosedError`
- `BidTooLowError`
- `BidderNotEligibleError`
- `DuplicateBidError`
- `AuctionAlreadySoldError`

---

## 13. Concurrency Tests
Executed via `ConcurrentBiddingEngineTest(TransactionTestCase)` against PostgreSQL:
1. `test_concurrent_two_bids_different_amounts`: Two simultaneous bids serialize cleanly; highest bid wins; current price is authoritative ($60.00). (PASS)
2. `test_concurrent_two_bids_same_amount`: Two simultaneous bids at identical amounts ($50.00); exactly one succeeds, the other is rejected with `BidTooLowError`. (PASS)
3. `test_concurrent_bids_near_end_trigger_anti_sniping`: Bids arriving near close dynamically extend `end_at` by 180s. (PASS)
4. `test_concurrent_bid_and_close_auction`: Concurrent bid and premature close; close is rejected because end_at has not passed. (PASS)
5. `test_multiple_concurrent_bids_increasing_amounts`: 4 concurrent threads with increasing amounts ($50, $60, $70, $80) serialize without deadlocks; final price is $80.00. (PASS)
6. `test_concurrent_buy_now_and_normal_bid`: Buy-now bid ($200.00) vs normal bid ($60.00); auction locks in `SOLD` status to buy-now bidder. (PASS)
7. `test_duplicate_bid_idempotency_key`: Same idempotency key submitted twice returns existing bid without duplicate records. (PASS)
8. `test_cross_tenant_concurrent_bids`: Independent concurrent bids on Tenant MY and Tenant AU execute with zero cross-tenant contamination. (PASS)
9. `test_celery_close_auction_task_execution_and_idempotency`: Celery close task marks expired auction as `SOLD`, sets winner, and is strictly idempotent when called repeatedly. (PASS)

---

## 14. Sequential Tests
11 domain sequential tests in `AuctionAndBiddingDomainTest`:
- Auction creation, starting price, min next bid
- Date validation (reject end before start)
- Cross-tenant listing/auction rejection
- Sequential increment tracking
- Anti-shill rule enforcement
- Non-active auction rejection
- Buy-now auto close
- Closing with winner
- Closing without bids (unsold)
- Cross-tenant isolation
- Delete protection (`models.PROTECT`)

---

## 15. Regression Tests
All 67 tests across all 5 test suites passed cleanly:
```text
Ran 67 tests in 4.546s
OK
```
- `apps.core`: 11 tests
- `apps.tenants`: 12 tests
- `apps.listings`: 5 tests
- `apps.bidding`: 20 tests (11 domain + 9 concurrency)
- `apps.telegram_engine`: 19 tests

---

## 16. PostgreSQL Verification
Verified direct connection against PostgreSQL 18:
```text
PostgreSQL status: OK, result: (1,)
```
Migrations `bidding.0001`, `bidding.0002`, `bidding.0003` are applied cleanly with composite idempotency indexes and constraints.

---

## 17. Redis Verification
Verified direct connection against Redis on `127.0.0.1:6379`:
```text
Redis status: OK, ping: True
```

---

## 18. Celery Verification
- `apps.core.tasks.health_check_task`: Executed and returned `status: ok`.
- `apps.bidding.tasks.close_auction_task`: Executed in Celery eager mode and verified in unit tests.

---

## 19. Health Endpoint Verification
- `GET /health/`: `200 OK` → `{'status': 'ok', 'application': 'ok', 'database': 'ok', 'redis': 'ok'}`
- `GET /health/live/`: `200 OK` → `{'status': 'alive'}`
- `GET /health/ready/`: `200 OK` → `{'status': 'ready', 'details': {'database': 'ready', 'redis': 'ready'}}`

---

## 20. Security Review
- Staged content scanned for secrets: No Telegram tokens, passwords, GitHub PATs, AWS keys, or `.env` files tracked.
- Anti-shill bidding prevents fraudulent price pumping.
- All monetary arithmetic uses `Decimal(12, 2)`.

---

## 21. Legacy Project Verification
The legacy directory `E:\auctionbots\CYG_Aquatics_Malaysia` was verified:
- Files modified during Phase 05: **0 files**.

---

## 22. Performance Considerations
- Row-level lock acquisition (`select_for_update`) is indexed on primary key `id`, ensuring sub-millisecond lock acquisition.
- Transactions are kept short (reads, updates, inserts only), avoiding connection pool starvation under high concurrent traffic.

---

## 23. Known Limitations
- Wallets and double-entry financial ledger deductions are deferred to Phase 06.
- Complex seller registration conversation FSMs are deferred to future feature phases.

---

## 24. Deferred Work
- **Phase 06 — Wallets & Financial Ledger**: Balance checks, deposits, commissions, refunds, escrow hold on bids.
- **Phase 07 — Dashboard UI**: Multi-tenant web portal.
- **Phase 08 — Legacy Data Migration**: Historic data migration from `CYG_Aquatics_Malaysia`.

---

## 25. Rollback Plan
If rollback of Phase 05 is necessary:
1. Revert Git commit: `git revert <phase-05-commit>`.
2. Roll back database migrations:
   ```bash
   python manage.py migrate bidding 0002_initial
   ```

---

## 26. Next Phase
**Phase 06 — Wallets & Financial Ledger**:
Implement tenant-scoped user wallets, double-entry financial ledger, deposit tracking, bid escrow reserves, and seller payout accounting.

---

## 27. Final Status
**PASS**: Production-grade concurrent bidding engine is implemented, fully tested under real multi-threaded PostgreSQL concurrency, verified, and documented.
