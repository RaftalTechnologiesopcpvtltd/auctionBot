# Phase 04 Audit — Multi-Tenant Telegram Engine

## 1. Objective
The objective of Phase 04 is to establish a secure, multi-tenant Telegram Bot engine foundation for AuctionBot (`E:\auctionbots\auctionBot`). The engine decouples incoming Telegram messaging updates from auction business rules, enabling multiple regional tenants (e.g., CYG Malaysia, AquaBid Australia) to run independent Telegram bot configurations on a unified Django application without cross-tenant interference or fragile long polling loops.

---

## 2. Scope
- Tenant-specific Telegram configuration (`TelegramBotConfig`).
- Secure cryptographic credential handling (AES-128-CBC + HMAC-SHA256 via `Fernet`) and 12-factor environment variable overrides.
- Deterministic tenant resolution via webhook URL routing (`/telegram/webhook/<tenant_slug>/<bot_type>/`).
- Secure webhook ingestion endpoint (`TelegramWebhookView`) with HTTP method enforcement and sanitized error handling.
- Webhook security via `X-Telegram-Bot-Api-Secret-Token` validation.
- Update dispatcher (`TelegramDispatcher`) routing `/start`, `/help`, unrecognized commands, general text messages, and callback queries.
- Tenant-scoped Telegram user identity (`TelegramUser`).
- Webhook update idempotency and deduplication (`TelegramUpdateLog`).
- Tenant-aware Telegram service abstraction (`TelegramService`) with mockable HTTP client for test isolation.
- Comprehensive test suite ensuring zero regressions across Phases 01, 02, 03, and 04.

---

## 3. Legacy Telegram Components Inspected
Extensive read-only inspection was performed on `E:\auctionbots\CYG_Aquatics_Malaysia`:
- `fish_registration.py`: Standalone seller registration script running on `python-telegram-bot` with 36 conversational states in long polling loop.
- `show_bidding_list.py`: Standalone buyer bidding script running on `pyTelegramBotAPI` (`telebot`) in a blocking `bot.polling()` loop.
- `home/models.py`: Inspected `Settings` model storing unencrypted plaintext bot tokens in `TextField` with hardcoded defaults.
- `start_bots.sh` & `addaqua.sh`: Shell scripts launching disparate Python processes under `nohup` with process ID files and ad-hoc restart watchdogs.
- `report.md`: Inspected architectural pain points and recommendations to replace polling with a centralized webhook engine.

---

## 4. Business/Integration Rules Discovered
1. **Separation of Interface from Domain**: Telegram is strictly an interface channel. Business rules (listing approvals, bids, pricing, state transitions) must reside in domain services, not in bot handlers.
2. **Deterministic Multi-Tenancy**: Bot identity and webhook endpoints must determine tenant context deterministically before any update processing occurs. User-selected country menus are strictly rejected as security boundaries.
3. **Dual / Unified Bot Profiles**: The legacy architecture segregated buyer and seller bots. The new model supports `UNIFIED`, `BUYER`, and `SELLER` roles per tenant.
4. **Idempotency**: Network retries from Telegram must not result in duplicate bid placements or duplicate messages.
5. **Stable User Identifiers**: Telegram numeric user IDs (`from_user.id`) are the only stable identifiers; usernames can change or be null.

---

## 5. Telegram Configuration Model
Implemented in `apps.telegram_engine.models.TelegramBotConfig`:
- Inherits `TenantOwnedModel` with `on_delete=models.PROTECT`.
- Fields:
  - `tenant`: `ForeignKey('tenants.Tenant', on_delete=models.PROTECT)`
  - `bot_type`: `CharField(choices=BotType.choices, default=BotType.UNIFIED)`
  - `bot_username`: `CharField(max_length=64, blank=True)`
  - `token_encrypted`: `TextField(blank=True)`
  - `token_env_var`: `CharField(max_length=128, blank=True)`
  - `webhook_secret_token`: `CharField(max_length=256, blank=True)`
  - `is_active`: `BooleanField(default=True)`
- Constraints: `UniqueConstraint(fields=['tenant', 'bot_type'], name='unique_tenant_bot_type')`
- Index: `Index(fields=['tenant', 'is_active', 'bot_type'])`
- Admin security: Excluded from form fields; masked property display (`1234...9876`).

---

## 6. Tenant Resolution
- Routed via explicit URL path parameters:
  - `POST /telegram/webhook/<slug:tenant_slug>/`
  - `POST /telegram/webhook/<slug:tenant_slug>/<str:bot_type>/`
- Looks up active `Tenant` by slug: `Tenant.objects.get(slug=tenant_slug, is_active=True)`.
- If tenant is not found or inactive, immediately returns HTTP `404 Not Found`.

---

## 7. Webhook Architecture
Implemented in `apps.telegram_engine.views.TelegramWebhookView`:
1. Enforces HTTP POST (`405 Method Not Allowed` for any other method).
2. Parses request JSON (`400 Bad Request` on malformed payload).
3. Resolves tenant and active `TelegramBotConfig` (`404 Not Found` if missing or disabled).
4. Verifies `X-Telegram-Bot-Api-Secret-Token` header against `bot_config.webhook_secret_token` (`403 Forbidden` on mismatch).
5. Validates integer `update_id` (`400 Bad Request` if missing).
6. Evaluates idempotency via `TelegramUpdateLog` (`200 OK` with `status: duplicate` on duplicate).
7. Dispatches to `TelegramDispatcher`.
8. Returns clean `200 OK` JSON response.

---

## 8. Dispatcher Architecture
Implemented in `apps.telegram_engine.dispatcher.TelegramDispatcher`:
- Thin dispatcher pattern receiving `tenant`, `bot_config`, and `telegram_service`.
- Dispatches:
  - `message`: Synchronizes `TelegramUser`, extracts `/command` or passes to text handler.
    - `/start`: Returns welcome greeting containing tenant name, currency, and timezone.
    - `/help`: Returns tenant support overview.
    - Unrecognized commands: Returns polite guidance.
    - General text messages: Extensible text handler.
  - `callback_query`: Synchronizes `TelegramUser`, acknowledges query via `answer_callback_query`, and routes callback payload.

---

## 9. Telegram Identity
Implemented in `apps.telegram_engine.models.TelegramUser`:
- Subclasses `TenantOwnedModel`.
- Fields:
  - `tenant`: `ForeignKey('tenants.Tenant', on_delete=models.PROTECT)`
  - `telegram_user_id`: `BigIntegerField(db_index=True)`
  - `chat_id`: `BigIntegerField(db_index=True)`
  - `username`: `CharField(max_length=64, blank=True, null=True)`
  - `first_name`: `CharField(max_length=128, blank=True)`
  - `last_name`: `CharField(max_length=128, blank=True)`
  - `language_code`: `CharField(max_length=16, blank=True)`
  - `is_blocked`: `BooleanField(default=False)`
- Constraints: `UniqueConstraint(fields=['tenant', 'telegram_user_id'], name='unique_tenant_telegram_user')`.
- Tenant Scoping: The same physical Telegram user interacting with CYG Malaysia and AquaBid Australia maintains distinct profiles and settings per tenant.

---

## 10. Security
- **Token Encryption**: Tokens encrypted using standard `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256) keyed by SHA-256 derived from Django `SECRET_KEY` or `TELEGRAM_TOKEN_ENCRYPTION_KEY`.
- **Environment Overrides**: Secret tokens can be injected via environment variables without storing plaintext tokens in databases.
- **Webhook Header Verification**: Strict matching of `X-Telegram-Bot-Api-Secret-Token` prevents spoofed webhooks.
- **Admin Masking**: Tokens are never rendered in cleartext in Django admin.
- **Sanitized Outputs**: Error responses return generic messages and never expose stack traces or secret keys.

---

## 11. Idempotency
Implemented in `apps.telegram_engine.models.TelegramUpdateLog`:
- Composite unique constraint: `(tenant, update_id)`.
- When an update with an existing `(tenant, update_id)` arrives:
  - Immediate `200 OK` return with `{"status": "duplicate", "update_id": ...}`.
  - Dispatching is suppressed, preventing duplicate bid creation or duplicate outbound messages.

---

## 12. Domain Boundary
- Telegram handlers in `apps/telegram_engine/` contain **zero** direct SQL or business calculations.
- No direct manipulation of `Auction.objects`, `Bid.objects`, or `Listing.objects` inside handlers.
- Future business actions will call `services.listings`, `services.bids`, and `services.auctions`.

---

## 13. Tests
All tests executed cleanly with zero failures or errors:
```text
Ran 58 tests in 1.956s
OK
```

Breakdown:
- `apps.core`: 11 tests (startup, configuration, PostgreSQL, Redis, Celery tasks, health endpoints)
- `apps.tenants`: 12 tests (model attributes, unique codes/slugs, IANA timezone validation, isolation, protected deletes)
- `apps.listings`: 5 tests (listing creation, approval, rejection, quantity invariants, tenant isolation)
- `apps.bidding`: 11 tests (auction creation, temporal validation, tenant mismatch rejection, incremental bidding, non-active rejection, buy-now auto-close, closing with winner, unsold closing, anti-shill rule)
- `apps.telegram_engine`: 19 tests:
  - Token encryption and clean decryption
  - Token masking
  - Configuration ownership
  - Environment variable token override
  - HTTP method validation (reject non-POST with 405)
  - Malformed JSON rejection (400)
  - Unknown tenant rejection (404)
  - Inactive tenant rejection (404)
  - Unconfigured / inactive bot rejection (404)
  - Missing or invalid secret token rejection (403)
  - Missing update_id rejection (400)
  - Valid /start command dispatch and TelegramUser upsert
  - Valid /help command dispatch
  - Callback query dispatch and acknowledgement
  - Duplicate update_id idempotency
  - Cross-tenant user and update isolation
  - TelegramService token requirement validation
  - Mocked TelegramService message formatting
  - Mocked TelegramService callback answering

---

## 14. PostgreSQL Verification
Verified direct connection against PostgreSQL 18:
```text
PostgreSQL status: OK, result: (1,)
```
Tables `telegram_engine_telegrambotconfig`, `telegram_engine_telegramuser`, and `telegram_engine_telegramupdatelog` are persisted with valid primary keys, foreign keys, and indexes.

---

## 15. Redis Verification
Verified direct Redis connection against `127.0.0.1:6379`:
```text
Redis status: OK, ping: True
```

---

## 16. Celery Regression
Verified Celery task pipeline:
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
Verified that `E:\auctionbots\CYG_Aquatics_Malaysia` remains completely untouched:
- Non-git files modified in legacy directory during Phase 04: **0 files**.

---

## 19. Production Safety Verification
- No real bot tokens were activated or registered with Telegram.
- No public webhook registrations (`setWebhook`) were called.
- No production servers, DNS records, or running bots were altered.

---

## 20. Secrets Review
- Verified no Telegram bot tokens, passwords, GitHub PATs, AWS credentials, or `.env` files are tracked in git.
- Test tokens use dummy strings (`TEST_FAKE_TOKEN_...`).
- Tokens are encrypted at rest.

---

## 21. Known Limitations
- High-concurrency Redis mutexes for atomic bid placement are not part of this phase (deferred to Phase 05).
- Anti-sniping dynamic time extensions are deferred to Phase 05.
- Complex seller registration conversation FSMs are deferred to future feature phases.

---

## 22. Deferred Work
- **Phase 05 — Concurrent Bidding Engine**: Redis distributed locking, atomic Lua scripts, anti-sniping dynamic extensions, Celery closing worker.
- **Phase 06 — Wallets & Accounting**: Double-entry ledger, deposits, payment webhooks, commissions.
- **Phase 07 — Dashboard UI**: Multi-tenant administrative and analytics portal.
- **Phase 08 — Legacy Data Migration**: Safe migration from `CYG_Aquatics_Malaysia`.

---

## 23. Rollback Plan
If rollback of Phase 04 is required:
1. Revert Git commit: `git revert <phase-04-commit>`.
2. Roll back database migrations:
   ```bash
   python manage.py migrate telegram_engine zero
   ```
3. Remove `apps.telegram_engine.apps.TelegramEngineConfig` from `config/settings/base.py`.
4. Remove `telegram/` route from `config/urls.py`.

---

## 24. Next Phase
**Phase 05 — Concurrent Bidding Engine**:
Implement atomic bid placement, Redis distributed mutexes, anti-sniping extensions, and asynchronous Celery auction closing workers.

---

## 25. Final Status
**PASS**: Multi-tenant Telegram Engine foundation is fully implemented, verified, tested, and documented.
