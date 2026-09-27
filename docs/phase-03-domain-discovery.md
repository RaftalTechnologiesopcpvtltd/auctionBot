# Phase 03 Domain Discovery

**Source System**: `E:\auctionbots\CYG_Aquatics_Malaysia` (Read-Only Reference)  
**Inspection Date**: 2026-09-27  
**Scope**: Core domain entities, business workflows, bidding rules, lifecycle transitions, and technical debt.

---

## 1. Legacy Components Inspected

1. `home/models.py`:
   - Primary data models: `Fish`, `AcceptedFishListings`, `RejectedFishListings`, `Bid`, `BuyNowPurchase`, `MakeBuyOffer`, `MakeAuctionOffer`, `AuctionWon`, `BuyerLimitBid`, `PaymentWallet`, `UnapprovedWallet`, `BuyerContactDetails`, `AdminPrice`, `BlockedSellers`, `BlockedBuyers`.
2. `show_bidding_list.py`:
   - Bidding client bot: Bidding workflows, increment calculation (`create_bid_amount_buttons`), bid submission (`handle_bid_amount`), auto-bidding limits (`BuyerLimitBid`), wishlist handling, Buy-It-Now execution, offer negotiation.
3. `fish_registration.py`:
   - Seller submission bot: Listing creation state machine, category branching (Auction vs. Buy-It-Now), pricing attributes, listing fee calculation, payment proof verification.
4. `home/cron.py`:
   - Lifecycle daemon: Periodic status transitions (`update_listing_status`), 15-minute reminders, auction completion and winner determination (`send_success_message_to_winner`), `AuctionWon` persistence.
5. `report.md`:
   - Architectural forensic audit documenting the duplicate legacy instances (`CYG_Aquatics_Malaysia`, `AddAqua_Australia`, `CYG_Manager`), hardcoded configurations, and target schema recommendations.
6. `PERFORMANCE_FIXES_SUMMARY.md`:
   - Optimization audit highlighting N+1 query patterns, Python-level filtering pitfalls, and index deficiencies in legacy bidding queries.

---

## 2. Core Business Entities

From the verified legacy codebase, the business domain comprises the following conceptual entities:

1. **Seller**:
   - Represented in legacy by `telegram_user_id`, `telegram_username`, and linked to `PaymentWallet`.
   - Submits listings, manages stock, receives notifications of sales/offers.
2. **Buyer / Bidder**:
   - Represented in legacy by `BuyerContactDetails` (`telegram_user_id`, `telegram_username`, `details`).
   - Places bids, executes Buy-It-Now orders, submits offers, sets auto-bid limits.
3. **Listing (Catalog Item)**:
   - Represented by `Fish` model.
   - Contains item title, description, species/breed, location, shipping instructions, and general attributes.
   - Operates in one of two commercial modes: **Auction** or **Buy It Now**.
4. **Auction**:
   - In legacy, split between `Fish` (auction parameters: start price, min bid, duration) and `AcceptedFishListings` (runtime lifecycle: `start_at`, `end_at`, `status`, `auction_current_amount`).
   - Governs the time-bound bidding window and current winning amount.
5. **Bid**:
   - Represented by `Bid` model.
   - Captures monetary increment/amount, timestamp, bidder identity, and the resulting auction total (`current_amount`).
6. **Auto-Bid Limit**:
   - Represented by `BuyerLimitBid`.
   - Stores a buyer's maximum ceiling for automated incremental counter-bidding on an auction.
7. **Purchase / Win**:
   - `AuctionWon`: Outcome record created when an auction successfully closes with a winning bidder.
   - `BuyNowPurchase`: Instant purchase record reducing listing stock.
8. **Offer Negotiation**:
   - `MakeAuctionOffer` and `MakeBuyOffer`: Counter-offers submitted by buyers below asking price, subject to manual or auto-acceptance.

---

## 3. Entity Relationships

```text
Seller (User)
   │
   └── owns ──> Listing (Item details, media, category)
                    │
                    ├── specifies ──> Auction (start_at, end_at, starting_price, min_increment)
                    │                    │
                    │                    ├── receives ──> Bid (bidder, amount, placed_at)
                    │                    ├── limits   ──> AutoBidLimit (bidder, max_amount)
                    │                    └── yields   ──> AuctionWon (winner, final_price, closed_at)
                    │
                    └── or specifies ──> Fixed Price (buy_now_price, stock_quantity)
                                            │
                                            └── yields ──> BuyNowPurchase (buyer, quantity, unit_price)
```

**Cardinality**:
- 1 Seller has Many Listings.
- 1 Listing has exactly 1 Auction (or 1 FixedPrice sale).
- 1 Auction has Many Bids.
- 1 Auction has Many AutoBidLimits (at most 1 per unique bidder).
- 1 Auction yields at most 1 `AuctionWon` result.

---

## 4. Auction Lifecycle

Verified from `AcceptedFishListings.calculate_status()` and `home/cron.py`:

```
                 [ DRAFT ] (Seller fills initial details)
                     │
                     v
                [ PENDING ] (Awaiting admin approval & listing fee payment)
                     │
                     v (Admin approved / payment verified)
             +-------+-------+
             |               |
(now < start_at)   (start_at <= now <= end_at)
             v               v
        [ SCHEDULED ]      [ ACTIVE / OPEN ]
             │               │
             +------->-------+ (now reaches start_at)
                             │
                             v (now > end_at OR stock exhausted OR auto-buyout)
                         [ CLOSED ]
                             │
                  +----------+----------+
                  v                     v
            (Has valid bids)       (No bids placed)
                  │                     │
                  v                     v
            [ SOLD / WON ]        [ UNSOLD / EXPIRED ]
```

### Lifecycle States:
1. **DRAFT**: Listing initialized but incomplete.
2. **PENDING**: Submitted by seller, awaiting approval or listing fee clearance.
3. **SCHEDULED**: Approved with a future `start_at` datetime.
4. **ACTIVE / OPEN**: Current time falls between `start_at` and `end_at`. Bids are accepted.
5. **CLOSED**: Auction duration expired or listing ended. Bids locked.
6. **SOLD**: Closed with a qualified winning bid (`AuctionWon` generated).
7. **UNSOLD**: Closed with zero bids.

---

## 5. Bid Rules

1. **Starting Price (`starting_price`)**:
   - Minimum acceptable opening price.
   - If no previous bids exist, opening bid must be $\ge$ `starting_price`.
2. **Minimum Bid Increment (`min_bid` / `bid_increment`)**:
   - Seller specifies a minimum bid increment (e.g., $5, $10, $50).
   - In legacy `create_bid_amount_buttons()`, increments are offered in multiples of `min_bid`: `[min_bid, 2*min_bid, ..., 6*min_bid]`. If unset, defaults to `[5, 10, 20, 30, ...]`.
3. **Bid Calculation (`new_auction_current_amount`)**:
   - `new_current_price = previous_current_price + selected_increment`.
   - Every subsequent bid must strictly exceed the current highest bid.
4. **Highest Bid Tracking**:
   - High bidder tracked by ordering bids: `Bid.objects.filter(listing=...).order_by('-current_amount', '-placed_at').first()`.
5. **Winner Determination**:
   - At `end_at`, the bidder with the highest valid `current_amount` is declared winner.
   - Persisted to `AuctionWon(listing, price, telegram_user_id, telegram_username)`.

---

## 6. Seller Rules

1. **Listing Fees (`listing_charges`)**:
   - Seller is assessed a listing fee based on listing duration (e.g. 1-day, 2-day, 3-day, 5-day, 10-day rates defined in `AdminPrice`).
   - In legacy, seller pays from `PaymentWallet` balance or submits manual bank transfer proof (`UnapprovedWallet`).
2. **Seller Self-Bidding Prohibition**:
   - Sellers cannot place bids on their own listings.
3. **Contact Information**:
   - Seller provides payment/delivery instructions disclosed to the auction winner upon close.

---

## 7. Buyer Rules

1. **Registration & Contact Details**:
   - Buyers provide contact/phone details (`BuyerContactDetails`) before bidding or buying.
2. **Auto-Bidding Ceiling (`BuyerLimitBid`)**:
   - Buyers can set a maximum proxy limit. When outbid, the system automatically counter-bids on their behalf up to their maximum ceiling.
3. **Outbid Notification**:
   - When a new highest bid is accepted, the previous highest bidder is notified that they have been outbid.

---

## 8. Product / Listing Rules

1. **Taxonomy & Attributes**:
   - Legacy attributes tailored for ornamental fish: `breed`, `size`, `gender`, `country_of_origin`, `location`, `postage`.
   - Domain requirement: Core listings must support clean general listing titles, descriptions, and extensible metadata without hardcoding fish-specific fields into the base auction engine.
2. **Media**:
   - Listings support primary photo, multiple gallery photos (`FishImage`), and video URLs/files.
3. **Stock Control**:
   - `quantity` and `left_quantity`. For single-item auctions, quantity is 1.

---

## 9. Validation Rules

1. **Monetary Precision**:
   - Must use `DecimalField(max_digits=12, decimal_places=2)`. Floating-point numbers are prohibited.
2. **Time Ranges**:
   - `end_at` must be strictly greater than `start_at`.
3. **Non-Negative Amounts**:
   - `starting_price > 0`, `min_bid_increment > 0`, `bid_amount > 0`.
4. **State-Locked Bidding**:
   - Bids must be rejected unless the auction status is strictly `ACTIVE`.

---

## 10. Scheduling Rules

1. **Periodic Evaluation**:
   - In legacy, a 5-second polling loop in `home/cron.py` evaluated `calculate_status()`.
   - In the target modern architecture, Celery Beat periodic tasks or event-driven triggers will manage state transitions.
2. **Pre-Close Reminders**:
   - Legacy triggers a reminder message 15 minutes prior to `end_at` (`is_reminder_sent = True`).

---

## 11. Financial Rules

1. **Currency Multi-Tenancy**:
   - Malaysia tenant uses `MYR` (Ringgit). Australia tenant uses `AUD` (Australian Dollars).
   - All monetary models must inherit the Tenant's currency context.
2. **Post-Auction Settlement**:
   - Legacy did not have automated escrow; winners received seller contact details to coordinate off-platform payment.
   - Full wallet/ledger integration is deferred to Phase 05.

---

## 12. Telegram Responsibilities (To Be Decoupled)

In legacy, Telegram UI logic and database transactions were completely conflated in `fish_registration.py` and `show_bidding_list.py`:
- User inputs parsed directly from Telegram callbacks into raw SQL/ORM calls.
- HTML formatting embedded directly in business calculations.
- The new domain architecture **must be completely independent of Telegram**. All domain models and services must operate headlessly.

---

## 13. Tenant Differences (Discovered from Codebase)

1. **Deployment Copies**:
   - `CYG_Aquatics_Malaysia`: Configured for Malaysia (Ringgit, MY bot tokens, Asia/Kuala_Lumpur timezone).
   - `AddAqua_Australia` (referenced in shell scripts `addaqua.sh`, `cron_addaqua.log`): Configured for Australia (AUD, Australia bot tokens, Australia/Sydney timezone).
2. **Conclusion**:
   - Both legacy instances ran identical business logic but in separate, duplicated scripts and databases.
   - The unified architecture must serve both via tenant-aware models in the single database.

---

## 14. Legacy Technical Debt

| Legacy Problem | Legacy Implementation | New Architecture Solution |
| :--- | :--- | :--- |
| **Float/String Prices** | Prices stored as `CharField(max_length=255)` in `Fish` model | Use `DecimalField(max_digits=12, decimal_places=2)` across all entities |
| **String Date Formats** | `auction_start_date` and `auction_start_time` stored as string text | Use proper timezone-aware `DateTimeField` |
| **Monolithic Model** | `Fish` contained auction attributes, buy-now attributes, and media | Separate into clean modular models (`Listing`, `Auction`, `Bid`) |
| **Unprotected Deletes** | `on_delete=models.CASCADE` everywhere | Use `models.PROTECT` on tenants and critical financial associations |
| **Duplicate Repositories** | Separate codebase copies for Australia and Malaysia | Single multi-tenant database and unified domain engine |
| **Embedded Telegram Code** | All bidding rules embedded in Telegram handlers | Headless domain service layer (`services/auctions.py`, `services/bids.py`) |

---

## Step A Stop Check: VERIFIED

The core business entities, lifecycles, and bidding rules have been thoroughly verified from the legacy source. Ready to proceed to **Step B: New Domain Architecture**.
