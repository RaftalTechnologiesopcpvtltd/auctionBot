# Phase 07 — Management Dashboard Architecture

## 1. Overview & System Scope

The **Management Dashboard & Frontend** (Phase 07) delivers an administrative web interface for the unified multi-tenant auction platform. It sits directly on top of the domain and application services implemented in Phases 01–06 (Tenants, Catalog & Listings, Bidding Engine, Telegram Engine, and Financial Double-Entry Ledger).

### Core Architecture Principles
1. **Direct Service Delegation**: No financial, auction, bidding, or tenancy business logic is duplicated inside the frontend or views. Mutations delegate directly to `apps.finance.services`, `services.auctions`, `services.bids`, and `services.listings`.
2. **Strict Multi-Tenant Isolation**: The management dashboard enforces tenant isolation at the request context layer via `apps.dashboard.context_processors.active_tenant_context` and session switcher (`active_tenant_id`). All listing, auction, bid, telegram, wallet, and ledger queries are strictly scoped by `tenant=request.tenant`.
3. **Double-Entry Financial Transparency**: The dashboard displays real-time balances from `FinancialAccount` and granular double-entry transaction ledgers (`LedgerEntry`). Financial actions (refunds, reversals) are verified for balance equality (`Total Debits == Total Credits`) and execute compensating journal entries rather than updating historical records.
4. **Token & Secret Masking**: Telegram bot API tokens, webhook secret tokens, and payment credentials are never exposed in plain text in HTML DOM or API responses. They are masked with bullet characters (`&bull;&bull;&bull;&bull;` / `••••`).

---

## 2. Technology Stack & Design System

- **Backend / Web Server**: Python 3.11, Django 5.2.x.
- **Frontend Strategy**: Server-Side Rendered (SSR) Django Templates with Tailwind CSS utility framework and Chart.js for real-time executive graphs.
  - Zero Node.js / npm build step required in deployment pipeline.
  - Native CSRF token protection on all mutative forms.
  - High responsiveness across desktop (1440px+), laptop, tablet (768px), and mobile viewports.
- **Visual Design Identity**:
  - Color Hierarchy: Deep Navy sidebar (`#0f172a`), clean slate background (`#f8fafc`), crisp white cards with rounded-2xl radii (`#ffffff`), and emerald/blue interactive accents.
  - Typography: Clean sans-serif hierarchy using Inter typography tokens.
  - Header: Tenant context switcher, dynamic Telegram Bot connection status indicator (`Bot Online` / `Bot Disconnected`), pending listing notifications bell, and user authentication dropdown.

---

## 3. Screen Inventory & Route Mapping

The dashboard fully implements all 17 reference designs cataloged from `frontend screens/`:

| Screen # | Screen File | Route Name | URL Path | Backend Service Integration |
|---|---|---|---|---|
| **01** | `66.jpeg` / `77.jpeg` | `dashboard:home` | `/dashboard/` | Aggregates KPIs, Chart.js revenue bars & sales donut, recent listings, recent bids, bot activity. |
| **02** | `22.jpeg` | `dashboard:auctions_list` | `/dashboard/auctions/` | `apps.bidding.models.Auction`, server-side status tab filtering, pagination, search. |
| **03** | `11.jpeg` | `dashboard:auction_detail` | `/dashboard/auctions/<id>/` | Detailed auction lifecycle, current price, bid stream, financial clearing status, end/cancel actions. |
| **04** | `555.jpeg` | `dashboard:listings_list` | `/dashboard/listings/` | `apps.listings.models.Listing`, tab filter (`pending`, `accepted`, `rejected`, `closed`), search. |
| **05** | `44.jpeg` | `dashboard:listing_detail` | `/dashboard/listings/<id>/` | Catalog item review, description, photos, accept / reject moderation controls. |
| **06** | `0.jpeg` | `dashboard:bids_list` | `/dashboard/bids/` | `apps.bidding.models.Bid`, full bid audit trail with bidder ID, amount, and timestamp. |
| **07** | `9.jpeg` | `dashboard:sellers_list` | `/dashboard/sellers/` | Aggregates sellers by active tenant, listings count, active auction count. |
| **08** | `2.jpeg` | `dashboard:users_list` | `/dashboard/users/` | Staff and platform administrators, add team member modal with role selection. |
| **09** | `5.jpeg` | `dashboard:telegram_overview` | `/dashboard/telegram/` | Bot health, webhook status, quick actions, masked token, recent bot update events. |
| **10** | `4.jpeg` | `dashboard:telegram_settings` | `/dashboard/telegram/settings/` | Bot handle, masked token updater, anti-sniping duration, auto-close configurations. |
| **11** | `3.jpeg` | `dashboard:telegram_messages` | `/dashboard/telegram/messages/` | Broadcast announcement composer, dispatch logs, delivery counters. |
| **12** | `8.jpeg` | `dashboard:telegram_users` | `/dashboard/telegram/users/` | Telegram registered buyers and sellers, ban/unban moderation controls. |
| **13** | `6.jpeg` | `dashboard:wallets_list` | `/dashboard/wallets/` | User wallets, platform cash account, available vs. held balance breakdown. |
| **14** | `7.jpeg` | `dashboard:transactions_list` | `/dashboard/transactions/` | Double-entry transaction history, status badges, idempotency references. |
| **15** | Detail Screen | `dashboard:transaction_detail` | `/dashboard/transactions/<id>/` | Double-entry ledger audit: balanced DEBIT/CREDIT breakdown, compensating refund trigger. |
| **16** | Analytics | `dashboard:reports` | `/dashboard/reports/` | Dynamic aggregation of gross volume, cleared bids, active wallets, print/PDF export. |
| **17** | `WhatsApp...04.12.10.jpeg` | `dashboard:settings` | `/dashboard/settings/` | Organization profile, currency, anti-sniping window, platform commission rate. |
| **18** | `WhatsApp...1.jpeg` | `dashboard:helpdesk` | `/dashboard/helpdesk/` | Customer inquiries, dispute resolution tickets, Tier 3 support escalation. |

---

## 4. Authentication & Role-Based Authorization

1. **Authentication**:
   - Enforced by `@dashboard_auth_required` decorator on all view endpoints.
   - Unauthenticated requests are immediately redirected to `/dashboard/login/?next=<path>`.
   - Dedicated login (`dashboard:login`) and logout (`dashboard:logout`) endpoints utilizing standard Django session authentication.
2. **Authorization**:
   - Only users with `is_staff=True` or `is_superuser=True` are permitted access.
   - Unauthorized users attempting access are redirected to login with explicit error notification.
   - Critical system configurations (e.g. adding staff users, issuing refunds) require staff or superuser privileges verified server-side.

---

## 5. Security & Isolation Controls

- **Tenant Isolation**:
  - `request.tenant` context processor automatically selects the active tenant from session (`active_tenant_id`), falling back to the first active tenant in database.
  - Multi-tenant staff can switch context instantly using the top navigation dropdown (`dashboard:switch_tenant`).
  - All database queries filter strictly on `tenant=request.tenant`. Cross-tenant queries are blocked.
- **Secret Protection**:
  - `TelegramBotConfig` tokens are stored encrypted using AES-128-CBC + HMAC-SHA256.
  - Neither templates nor view contexts ever output plaintext bot tokens or webhook secrets.
- **Financial Immutability**:
  - Ledger accounts and transactions cannot be updated or deleted via HTTP `PUT` or `DELETE`.
  - Financial adjustments execute only through `apps.finance.services.refund()` and `reverse_transaction()`, creating immutable compensating entries.

---

## 6. Verification & Automated Test Coverage

The dashboard includes a dedicated automated test suite in `apps/dashboard/tests.py` verifying:
- Anonymous user redirection and staff authentication.
- Tenant context switching and cross-tenant data isolation.
- Domain view rendering with live database queries (auctions, listings, bids, sellers, telegram, wallets, ledger transactions).
- Bot token masking security.
- Double-entry ledger reconciliation and balanced entries rendering.

Total automated platform test count: **110 tests** (92 baseline + 18 dashboard tests) running with **0 failures and 0 regressions**.
