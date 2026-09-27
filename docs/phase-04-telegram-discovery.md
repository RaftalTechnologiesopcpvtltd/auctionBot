# Phase 04 — Telegram Engine Discovery

## 1. Overview and Legacy Inspection Context
This document records the empirical findings from a read-only architectural inspection of the legacy auction system (`E:\auctionbots\CYG_Aquatics_Malaysia`). The investigation specifically examined bot configuration, runtime loops, user identification, command and callback routing, message transmission, and security practices.

Files inspected:
- `fish_registration.py` (4,057 lines, seller bot running on `python-telegram-bot`)
- `show_bidding_list.py` (3,221 lines, bidding/buyer bot running on `pyTelegramBotAPI`/`telebot`)
- `home/models.py` (`Settings`, `CustomAdminBotMessages`, `AdminPrice`, `PaymentWallet`, `BuyerContactDetails`)
- `start_bots.sh` (Bash process manager launching bots via `nohup python ...`)
- `addaqua.sh` (Bash launcher for Australia deployment variant)
- `cyg_manager.py` (Process management utility)
- `report.md` (Legacy audit and modernization architecture recommendations)

---

## 2. Bot Configuration and Credential Storage

### Legacy Implementation
- **Global Singleton Settings**: Bot tokens were retrieved from the `home_settings` table via `Settings.objects.first()`.
  ```python
  settings_obj = Settings.objects.first()
  TELEGRAM_API_KEY = settings_obj.seller_bot_token
  BIDDING_BOT_TOKEN = settings_obj.bidding_bot_token
  ```
- **Plaintext and Hardcoded Tokens**: In `home/models.py`, `Settings.seller_bot_token` and `Settings.bidding_bot_token` were defined as plain `models.TextField` with live Telegram bot tokens hardcoded as model field defaults (`default='7218004660:AAGS-...'`).
- **Dual-Bot Architecture**: The legacy system split responsibilities across two separate bots:
  1. *Seller Bot / Fish Registration*: Registered listings, managed seller wallets, collected fees.
  2. *Buyer Bot / Bidding List*: Displayed active listings, received bids, handled buy-now purchases, sent outbid alerts.

### Architectural Gap
- No encryption at rest for bot tokens.
- No multi-tenant scoping: `Settings` assumed exactly one tenant per database instance.
- Tokens were exposed directly in Django admin and database dumps.

---

## 3. Tenant and Regional Isolation

### Legacy Implementation
- **No In-Application Multi-Tenancy**: The application had zero tenant resolution logic.
- **File/Server Duplication**: Multi-country operations (e.g. Malaysia vs. Australia) were handled by duplicating scripts (`addaqua.sh`, `cron_addaqua.log`, `fish_registration_addaqua.log`, `runserver_addaqua.log`).
- **Hardcoded Formatting**: Currency symbols (`RM` vs. `$`) and timezones were hardcoded directly in string interpolation within message handlers.

### Architectural Gap
- Impossible to run multiple regional bots within a single application process without code duplication.
- No tenant context passed to Telegram message handlers.

---

## 4. Bot Execution and Polling Architecture

### Legacy Implementation
- **Long Polling Only**: Neither bot used Telegram webhooks. Both used long polling:
  - `fish_registration.py`: `application.run_polling()` (`python-telegram-bot` v20+ async event loop).
  - `show_bidding_list.py`: `while True: try: bot.polling() except Exception as e: time.sleep(5)` (`telebot` synchronous loop).
- **Library Incompatibility**: The legacy codebase ran two conflicting Telegram libraries (`python-telegram-bot` and `telebot` / `pyTelegramBotAPI`) concurrently in separate OS processes.
- **External Process Supervision**: Both bots were launched via bash scripts (`start_bots.sh`) using `nohup` and background PID files, monitored by an ad-hoc watchdog script.

### Architectural Gap
- Polling caused high latency (1–3 seconds per update), frequent socket timeouts, and process crashes under load.
- No unified HTTP webhook ingestion endpoint existed in Django.

---

## 5. Command and Callback Routing

### Commands Found
- `/start`: Initiated welcome greeting, role selection, or main menu keyboard.
- `/help`: Displayed usage instructions or contact details.
- `/cancel`: Cancelled active conversation states in `ConversationHandler`.
- `/helpdesk`: Opened a support ticket inquiry flow.

### Callback Queries Found
- Listing management: `delete_<id>`, `refresh_auction_listing_<id>`
- Offers & purchases: `accept_buynow_offer_<id>`, `reject_buynow_offer_<id>`, `accept_auction_offer_<id>`, `reject_auction_offer_<id>`
- Payments & wallet: `give_payment_details_`, `add_seller_money_<id>`, `wallet_amount_save_<id>`, `submit_yes_payment`, `cancel_no_payment`
- Bidding callbacks: Placing increments, buy-now confirmation, pagination of active lots.

### Routing Mechanism
- Handlers were tightly coupled to regex patterns matching raw callback data strings.
- In `show_bidding_list.py`, monolithic callback handlers (thousands of lines long) evaluated callback prefixes, queried SQLite directly, calculated bid increments, and called `bot.send_message` inline.

---

## 6. User Identity Handling

### Legacy Implementation
- **External Telegram Identifiers**: Telegram numeric user ID (`message.from_user.id`) and username (`message.from_user.username`) were captured directly from the Telegram update.
- **Disparate Storage**: The numeric ID was stored in various unstructured fields:
  - `Fish.seller_id = models.CharField(max_length=50)`
  - `Bid.telegram_user_id = models.CharField(max_length=50)`
  - `BuyerContactDetails.user_id = models.CharField(max_length=50)`
  - `PaymentWallet.user_id = models.CharField(max_length=50)`
- **No Unified Telegram User Entity**: There was no `TelegramUser` model linking an external Telegram account to an internal tenant or system user.

### Architectural Gap
- Unstable identity: If a user changed usernames, historical references broke or became inconsistent.
- No tenant scoping: A Telegram user was assumed global, preventing different profile states or permissions across regional tenants.

---

## 7. Message Transmission and Formatting

### Legacy Implementation
- Direct synchronous calls to Telegram HTTP API:
  - `requests.post(TELEGRAM_API_URL + 'sendMessage', ...)`
  - `bot.send_message(chat_id, text, reply_markup=...)`
- Inline keyboards and reply markups were manually constructed inside business logic blocks.
- Markdown/HTML parsing errors caused unhandled exceptions that aborted entire handler flows.

---

## 8. Webhook Security and Verification

### Legacy Implementation
- **Not Verified from Legacy Source**: Webhook secret tokens (`X-Telegram-Bot-Api-Secret-Token`), HTTPS verification, and update signature checks were completely absent because webhooks were not implemented in the legacy runtime.

---

## 9. Idempotency and Deduplication

### Legacy Implementation
- **No Deduplication**: Neither bot tracked `update_id`.
- If Telegram re-sent an update or if the network reconnected after a timeout, the bot re-processed the update, resulting in duplicate bid submissions or duplicate wallet deductions.

---

## 10. Summary of Architectural Decisions for New Telegram Engine (Phase 04)

1. **Clean Separation of Concerns**: Telegram handlers must only parse incoming updates, resolve the tenant, and invoke decoupled application/domain services. No direct database or business calculations inside handlers.
2. **Multi-Tenant Configuration Model (`apps.telegram_engine.models.TelegramBotConfig`)**:
   - Belongs to `Tenant` via `models.PROTECT`.
   - Stores bot credentials securely (environment-backed or encrypted-at-rest token references).
   - Stores webhook secret token for header validation (`X-Telegram-Bot-Api-Secret-Token`).
   - Flags for active status and bot type (`BUYER`, `SELLER`, `UNIFIED`).
3. **Webhook Ingestion Endpoint (`/telegram/webhook/<tenant_slug>/<bot_type>/`)**:
   - Replaces fragile long polling with instant, asynchronous HTTPS POST webhook ingestion.
   - Deterministically resolves tenant by `tenant_slug` and validates active status.
   - Validates `X-Telegram-Bot-Api-Secret-Token` before accepting payload.
4. **Tenant-Scoped Telegram User Identity (`apps.telegram_engine.models.TelegramUser`)**:
   - Unique constraint on `(tenant, telegram_user_id)`.
   - Preserves user metadata (`first_name`, `last_name`, `username`, `language_code`) cleanly per tenant.
5. **Update Idempotency (`apps.telegram_engine.models.TelegramUpdateLog`)**:
   - Enforces unique `(tenant, update_id)` to deduplicate retried updates deterministically.
6. **Unified Service Abstraction (`services.telegram.TelegramService`)**:
   - Encapsulates `send_message`, `answer_callback_query`, `edit_message_text`.
   - Requires explicit tenant context (`TelegramService(tenant)`).
   - Mockable interface ensuring zero live HTTP requests during automated testing.
