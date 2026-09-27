# Phase 05 — Concurrent Bidding Engine Discovery

## 1. Overview and Legacy Inspection Context
This document records empirical findings from the read-only inspection of the legacy auction system (`E:\auctionbots\CYG_Aquatics_Malaysia`), focusing on bidding mechanics, pricing arithmetic, concurrency handling, winner determination, closing schedules, and timing rules.

Files inspected:
- `show_bidding_list.py` (lines 2800–3221: bidding callback handlers, buy-now handlers, limit bids)
- `home/cron.py` (lines 20–220: periodic status checks, 15-minute reminders, closing and winner notifications)
- `home/models.py` (`AcceptedFishListings`, `Bid`, `AuctionWon`, `BuyNowPurchase`, `Settings`)
- `report.md` (concurrency audit notes and race condition vulnerabilities)

---

## 2. Legacy Bidding Flow
In `show_bidding_list.py`:
1. User taps an inline keyboard button with callback data formatted as `bid_amount_<listing_id>_<amount>`.
2. The handler extracts `listing_id` and `amount` as strings and casts them via Python `int()`.
3. The database is queried for `AcceptedFishListings.objects.filter(pk=listing_id).last()`.
4. If `auction_current_amount` is null/empty, the new amount is calculated as `int(cat_price) + amount`. Otherwise, it increments `accepted_listing.auction_current_amount + amount`.
5. A new `Bid` record is created synchronously via `Bid.objects.create(...)`.
6. The listing's `auction_current_amount` is updated and saved: `accepted_listing.save()`.
7. An outbound Telegram confirmation message is dispatched synchronously using `bot.send_message`.

---

## 3. Bid Validation
- **Status Check**: In some entry points, the query filters `status='Open'`, but in `handle_bid_amount` (line 2990), `AcceptedFishListings.objects.filter(pk=listing_id).last()` did NOT enforce `status='Open'`.
- **Minimum Bid Step**: The button values offered preset increment amounts (e.g., $1, $5, $10). However, there was no server-side lower-bound assertion verifying that a custom or manual bid satisfied `amount >= min_next_bid`.
- **Numeric Precision**: Python `int()` was used rather than `Decimal`, causing rounding/truncation bugs on fractional monetary amounts.

---

## 4. Minimum Increment Calculation
- The legacy model relied on `cat.auction_min_bid` (defined on the item catalog).
- Formula used:
  - If no bids placed: `opening_bid = starting_price + step` or `starting_price`.
  - If existing bids: `next_bid = current_amount + step`.

---

## 5. Self-Bidding Restrictions (Anti-Shill)
- In `show_bidding_list.py`, certain list display routines suppressed bid buttons if `call.from_user.id == cat.seller_id`.
- However, in `handle_bid_amount` (the actual execution handler at line 2962), **there was no check** preventing a seller from sending a raw `bid_amount_` callback to bid on their own listing.

---

## 6. Buy-Now Behavior
- Handled in `show_bidding_list.py` (lines 1920–2010):
  - User triggered `buy_now_<listing_id>`.
  - Bot requested confirmation: `confirm_yes_<listing_id>`.
  - On confirmation:
    - Reduced `left_quantity -= 1`.
    - If `left_quantity == 0`, set `accepted_listing.status = "Closed"`.
    - Created `BuyNowPurchase` record.
    - Sent seller notification and bidder congratulatory message.
- Non-atomic: No row locks or transaction blocks guarded the quantity reduction.

---

## 7. Auction Timing
- Starting and closing times were stored as string text (e.g. `'2026-09-27'` and `'18:00:00'`) or naive datetimes.
- Calculated in `calculate_status()`:
  - If `now < start_at` → `'Pending'`.
  - If `start_at <= now <= end_at` → `'Open'`.
  - If `now > end_at` → `'Closed'`.
- Time comparisons suffered from naive vs. aware timezone runtime warnings.

---

## 8. Auction Closing
- Executed by an external Python cron script (`home/cron.py`) running in an infinite loop or system cron:
  - Iterated over all `AcceptedFishListings` where `status != 'Closed'`.
  - Called `calculate_status()`.
  - If status changed to `'Closed'`, called `send_success_message_to_winner(listing)`.
- **Idempotency Weakness**: Closing relied on a boolean flag `is_closed` on the listing. If the script crashed mid-execution, duplicate winner alerts were sent upon restart.

---

## 9. Winner Determination
- In `home/cron.py` (lines 185–195):
  ```python
  last_bid = Bid.objects.filter(listing=listing).order_by('-current_amount', '-placed_at').first()
  ```
- The highest bidder according to `current_amount` was designated the winner, and an `AuctionWon` row was created.
- If zero bids existed, no winner was declared.

---

## 10. Anti-Sniping Behavior
- **Not verified from legacy source.**
- The legacy codebase contained zero anti-sniping or dynamic auction extension logic. An auction strictly closed at its scheduled `end_at`, regardless of bids received in the final seconds.

---

## 11. Duplicate Bid Behavior
- **No Idempotency Key**: The legacy system did not track client request identifiers or idempotency keys.
- If a Telegram user rapidly tapped the bid button or if Telegram delivered duplicate callback updates, multiple identical `Bid` records were created, repeatedly incrementing the price.

---

## 12. Error Behavior
- Errors were logged via `print()` or Python `logging.error`.
- In multiple places, unhandled database exceptions crashed the polling loop, requiring external watchdog supervision to restart the process.

---

## 13. Legacy Concurrency Limitations
- **No Database Locking**: Neither `select_for_update()` nor `transaction.atomic()` was utilized.
- **Race Condition Vulnerability**:
  - Two simultaneous bids at $10.00 would both read `current_amount = $10.00`.
  - Process A calculated `new_amount = $15.00` and wrote it.
  - Process B simultaneously calculated `new_amount = $15.00` and wrote it.
  - Both bids were recorded at $15.00, corrupting the bid ladder.
- **No Concurrency Safety on Buy-Now**: Two concurrent buy-now requests could both read `left_quantity = 1` and both succeed, overselling the lot.

---

## 14. Rules Carried Forward vs. Rules Intentionally Changed

### Rules Carried Forward
1. **Minimum Next Bid Formula**: First bid starts at or above `starting_price`; subsequent bids must be `>= current_price + bid_increment`.
2. **Immediate Buy-Now Closure**: A valid buy-now bid immediately halts bidding, awards the lot to the buyer, and transitions status to `SOLD`.
3. **Winner Determination**: Highest valid bid amount upon closing designates the winner; auctions with no bids conclude as `UNSOLD`.
4. **Anti-Shill Rule**: Sellers cannot bid on their own listings.

### Rules Intentionally Changed
1. **PostgreSQL Row-Level Locking (`select_for_update`)**: Mandatory pessimistic lock on the `Auction` row inside a database transaction (`transaction.atomic()`). Stale memory reads are strictly eliminated.
2. **Strict `Decimal(12, 2)` Monetary Arithmetic**: Replaces legacy integer casting with exact decimal precision.
3. **Timezone-Aware Timestamps**: All scheduling and duration checks use UTC timezone-aware datetimes.
4. **Idempotency Protection**: Introduces unique `idempotency_key` support on bids to eliminate duplicate submissions from network retries.
5. **Anti-Sniping Engine**: If a bid is submitted within the anti-sniping threshold window (e.g. final 2 minutes / 120 seconds), the auction `end_at` is extended by 120 seconds atomically under the row lock.
6. **Celery Asynchronous Auction Closing**: Replaces blocking cron loops with idempotent, retryable Celery tasks (`close_auction_task`).
7. **External Side-Effect Isolation**: Outbound notifications and Telegram messages are strictly forbidden inside database transactions.
