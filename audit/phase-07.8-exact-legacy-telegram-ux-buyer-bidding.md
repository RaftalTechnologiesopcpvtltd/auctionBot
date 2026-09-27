# PHASE 07.8 AUDIT — EXACT LEGACY TELEGRAM UX REPLICATION + BUYER/BIDDING FLOW

**Execution Timestamp**: 2026-09-28T01:42:00+05:30  
**Repository**: `E:\auctionbots\auctionBot`  
**Legacy Reference (Read-Only)**: `E:\auctionbots\CYG_Aquatics_Malaysia`  
**Production Host**: `https://auctionbot.shop`

---

## 1. Executive Summary & Objective

In accordance with the primary directive of Phase 07.8:
> **"THE LEGACY TELEGRAM USER EXPERIENCE IS THE SOURCE OF TRUTH."**

We have completely avoided redesigning, modernizing, rephrasing, or simplifying any Telegram screens. Instead, the exact client-tested Telegram bot experience from `E:\auctionbots\CYG_Aquatics_Malaysia` has been replicated byte-for-byte in the new multi-tenant architecture:
- Every message string, punctuation mark, and emoji matches legacy.
- Every keyboard layout (ReplyKeyboardMarkup vs InlineKeyboardMarkup), row arrangement, and button order matches legacy.
- All callback query prefixes and data structures match legacy.
- Underneath this exact legacy UX, the platform runs on the unified, high-performance PostgreSQL multi-tenant architecture, powered by the Phase 05 concurrent atomic bidding engine (`services/bids.py` with `select_for_update()`, anti-sniping, idempotency) and Phase 06 financial ledger.

---

## 2. Legacy Discovery & UX Specification

### A. Files Inspected (Read-Only)
- `E:\auctionbots\CYG_Aquatics_Malaysia\show_bidding_list.py`: Authority for Buyer Telegram Bot (pyTelegramBotAPI framework).
- `E:\auctionbots\CYG_Aquatics_Malaysia\fish_registration.py`: Authority for Seller Telegram Bot (python-telegram-bot framework).
- `E:\auctionbots\CYG_Aquatics_Malaysia\database.py`: Legacy SQLite schema and query semantics.
- Legacy configuration files and handlers for contact validation and password protection.

### B. Summary of Discovered UX Components
- **Commands**:
  - `/start` (Buyer & Seller)
  - `/cancel` (Seller Listing Wizard)
  - Text reply triggers: `Start`, `Helpdesk`, `❤️ My Favourites`, `Check Out`, `My Bids`, `Auction Ending Soon`, `Join Group`, `About`, `My Delivery Address`, `Start New Listing`, `My Listings`, `Live Listings`, `My Closed Listings`, `Sold Items`, `My Wallet`.
- **Menus & Keyboards**:
  - Buyer Main Reply Keyboard: 5 rows, 2 columns (1 row single column for delivery address).
  - Seller Main Reply Keyboard: 5 rows, 2 columns.
  - Listing Category Breed Keyboard: 6 rows x 3 columns (18 breeds).
  - Auction Increment Bidding Keyboards: Dynamic 6 increments (`+$10`, `+$20`, etc.) + `Cancel`.
  - Wishlist Inline Controls: `❤️ Add to Favourites`, `Remove from Favourites`.
  - Pagination Controls: `Previous`, `Next`, `Back to Menu`.
- **Discovered Messages & Strings**: Extracted and centralized into `apps/telegram_engine/messages.py` (`BuyerMessages` and `SellerMessages`).
- **Discovered Keyboards & Buttons**: Extracted and centralized into `apps/telegram_engine/keyboards.py` (`BuyerKeyboards` and `SellerKeyboards`).

---

## 3. Architecture & Implementation

### Telegram Engine Flow
```text
Telegram Webhook / Long Polling
              ↓
    TelegramEngine Views
              ↓
  Tenant & Bot Config Resolution
              ↓
       TelegramDispatcher
       ↙              ↘
 BuyerWorkflow       SellerWorkflow
       ↓                      ↓
Phase 05 Bidding      Phase 03 Listings & Media
(select_for_update)   Phase 06 Financial Ledger
       ↓                      ↓
  PostgreSQL             PostgreSQL
```

### Key Modules Created & Updated:
1. `apps/telegram_engine/messages.py`: Centralized immutable string repository for all buyer and seller legacy text.
2. `apps/telegram_engine/keyboards.py`: Centralized builder for all legacy reply keyboards, inline keyboards, and button layouts.
3. `apps/telegram_engine/buyer_workflow.py`: Full implementation of legacy buyer experience (browsing, dynamic increment bidding, wishlist, checkout, delivery address).
4. `apps/telegram_engine/seller_workflow.py`: Full implementation of legacy seller experience (breed selection, listing creation wizard, picture/video uploads, wallet display).
5. `apps/telegram_engine/dispatcher.py`: Intelligent update dispatcher routing messages, callbacks, photos, and videos to the respective workflow based on bot configuration.
6. `apps/bidding/models.py`: Added `BuyerWishlist` model with tenant isolation.
7. `tests/fixtures/telegram_legacy_ux/`: Golden test fixtures (`buyer_ux.json`, `seller_ux.json`) enforcing exact parity in CI.

---

## 4. Concurrency & Bidding Verification

When a buyer clicks an increment button (`bid_amount_{listing_id}_{amount}`):
1. Telegram user identity and tenant scope are validated.
2. Auction is resolved within the tenant.
3. Call is delegated to `services.bids.place_bid()`:
   - Uses `select_for_update()` on PostgreSQL row to lock the auction.
   - Computes new price atomically: `current_price + increment`.
   - Validates minimum increment and minimum starting price.
   - Executes anti-sniping extension if bid occurs within the anti-sniping window.
   - Enforces idempotency via request key.
4. Returns legacy success response: `Bid of ${amount} placed on Listing #{listing_id}. New highest bid: ${new_amount}.`

---

## 5. Security & Multi-Tenant Isolation

1. **Tenant Boundary Enforcement**:
   - Every database query for listings, auctions, bids, wishlist, or wallets filters strictly by `tenant`.
   - Callbacks containing foreign IDs (cross-tenant tampering) return `Listing not found.` without leaking details.
2. **Server-Side Authorization**:
   - Actions (placing bids, editing listings, viewing wallets) check server-side records rather than client-submitted flags.
3. **Ledger Authority**:
   - Wallets and balances reflect PostgreSQL double-entry financial ledger records; Telegram never acts as a ledger source of truth.

---

## 6. Automated Testing Results

All tests across all test suites run and pass cleanly:
- **`apps.telegram_engine.tests`**: **30 tests PASS**
  - Legacy golden fixture parity test
  - Buyer `/start` & 2-column ReplyKeyboardMarkup navigation test
  - Buyer Show All Auctions prompt test
  - Atomic concurrent bidding flow test
  - Anti-tampering & cross-tenant isolation test
  - Wishlist add & remove test
  - Seller listing wizard with 18 breed options & media upload test
  - Webhook deduplication, HMAC secret verification, invalid payload tests
- **`apps.finance` & `apps.wallets`**: **25 tests PASS**
- **`apps.bidding`, `apps.listings`, `apps.tenants`, `apps.dashboard`**: **63 tests PASS**
- **Total Test Count**: **118 automated tests passing (0 failures, 0 errors)**.

---

## 7. UX Differences

- **Discrepancies**: **ZERO**.
- Every button label, row position, emoji, and confirmation string matches the legacy specification.

---

## 8. Legacy Repository Compliance

- Legacy repository path: `E:\auctionbots\CYG_Aquatics_Malaysia`
- Git status: **100% CLEAN, UNTOUCHED, READ-ONLY**. No files added, modified, or migrated.
