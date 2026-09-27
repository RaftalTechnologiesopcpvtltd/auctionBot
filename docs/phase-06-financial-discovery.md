# Phase 06 — Financial Discovery

## 1. Overview and Legacy Inspection Context
This document records the empirical findings from a read-only architectural inspection of the legacy auction system (`E:\auctionbots\CYG_Aquatics_Malaysia`), specifically examining balance tracking, deposits, withdrawals, fees, payments, and accounting practices.

Files inspected:
- `home/models.py` (`PaymentWallet`, `UnapprovedWallet`, `AdminPrice`, `BuyNowPurchase`, `AcceptedFishListings`)
- `home/views.py` (lines 1210–1240, lines 2070–2135: listing fee deductions, wallet top-up approvals, manual balance edits)
- `fish_registration.py` (seller wallet balance check prior to lot submission)
- `show_bidding_list.py` (buy-now balance check and fee calculation)
- `report.md` (legacy audit notes on missing ledger trails and double-spend risks)

---

## 2. Legacy Financial Behavior Discovered

### A. Primitive Mutable Wallet
The legacy system relied entirely on a single database model:
```python
class PaymentWallet(models.Model):
    telegram_user_id = models.CharField(max_length=50, null=True, blank=True)
    telegram_username = models.CharField(max_length=150, null=True, blank=True)
    wallet_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, default='Not Deleted')
```
- **Direct Mutable State**: There was **no ledger**. All deposits, deductions, and adjustments directly modified `wallet_balance`:
  ```python
  wallet_entry.wallet_balance -= Decimal(price_to_deduct)
  payment_wallet.wallet_balance += unapproved_wallet.new_wallet_balance
  seller.wallet_balance = request.POST.get('wallet_balance')
  ```
- **Uncontrolled Deletion**: Accounts could be hard-deleted from Django admin or view endpoints (`PaymentWallet.objects.filter(id__in=ids).delete()`), obliterating all evidence of historical user balances.

### B. Deposit Workflow (`UnapprovedWallet`)
- Sellers topped up their wallet by manually uploading an image of a bank slip or payment screenshot (`payment_proof`).
- An admin reviewed the screenshot in a custom Django view and pressed "Approve".
- Upon approval, the view executed `payment_wallet.wallet_balance += unapproved_wallet.new_wallet_balance`.
- No audit log, idempotency key, or immutable ledger transaction was created.

### C. Listing Fee Deductions
- When a seller listed an item (`Fish`), the system deducted a listing fee (e.g. $1 per day of auction, defined in `AdminPrice`).
- The deduction was executed in an unmanaged transaction block without row-level locking (`select_for_update`).
- Overdraft checks (`if wallet_entry.wallet_balance < price_to_deduct:`) were evaluated in application memory before saving, leaving a race condition window where concurrent listing submissions could overdraft the wallet into negative numbers.

### D. Bidding and Buyer Payments
- In the legacy system, placing a bid did NOT reserve funds or hold escrow from the buyer.
- Buyers only settled off-platform or through post-auction contact with the seller:
  - The winner message simply sent: `"Please contact seller to finalise the payments. Here is the seller's contact information: ..."`.
- Buy-now purchases verified that the *seller* had enough balance to pay the platform commission percentage before completing the sale.

---

## 3. Current New-Project Financial Touchpoints
In AuctionBot (`E:\auctionbots\auctionBot`):
- `Tenant`: Owns an explicit ISO 4217 `currency` (e.g. `MYR`, `AUD`).
- `Listing`: Has an approved status and quantity.
- `Auction`: Tracks `starting_price`, `bid_increment`, `current_price`, `buy_now_price`, `winner_id`, and `winning_price` strictly using `DecimalField(max_digits=12, decimal_places=2)`.
- `Bid`: Stores immutable `amount` using `DecimalField(max_digits=12, decimal_places=2)`.
- `TelegramUser`: Scoped to `Tenant`, identifying users by stable 64-bit integer `telegram_user_id`.

Currently, no financial accounts, ledgers, or wallet balances exist in the new repository.

---

## 4. Risks Discovered in Legacy Architecture

| Risk Area | Legacy Defect | Impact |
| :--- | :--- | :--- |
| **No Ledger** | Balances are simple mutable fields | Impossible to reconstruct history or perform audit reconciliation |
| **Double Spend** | No `select_for_update()` row locking | Concurrent requests can spend the same balance twice |
| **Silent Mutation** | Admins can overwrite `wallet_balance` via text input | Fraud and human error cannot be traced |
| **Hard Deletes** | Wallets can be dropped with CASCADE | Audit trail destroyed |
| **Currency Mixing** | Currency symbols (`$`, `RM`) hardcoded in UI strings | Multi-currency corruption |
| **No Idempotency** | Repeating a request re-credits or re-debits funds | Network retries cause duplicate deductions |

---

## 5. Proposed Financial Architecture (Phase 06)

To permanently resolve these defects, Phase 06 establishes an **authoritative double-entry financial ledger**:

```text
                           Financial Transaction
                     (Atomic Event, Idempotent Key)
                                   │
                 ┌─────────────────┴─────────────────┐
                 ▼                                   ▼
          Ledger Entry 1                      Ledger Entry 2
       Account A (DEBIT 100)               Account B (CREDIT 100)
                 │                                   │
                 ▼                                   ▼
        Materialized Balance                Materialized Balance
         (Cache of Ledger)                   (Cache of Ledger)
```

1. **The Ledger is the Sole Source of Truth**:
   - `LedgerTransaction`: Represents an atomic financial event (`DEPOSIT`, `WITHDRAWAL`, `BID_HOLD`, `PURCHASE`, `REFUND`, `COMMISSION`, `FEE`, `ADJUSTMENT`, `REVERSAL`, `TRANSFER`).
   - `LedgerEntry`: Strictly immutable entries (`DEBIT` or `CREDIT`) with strictly positive `Decimal` amounts.
   - **Balanced Accounting**: For balanced double-entry transactions, `TOTAL DEBITS == TOTAL CREDITS`.
2. **Financial Accounts (`FinancialAccount`)**:
   - Belongs to `Tenant` via `on_delete=models.PROTECT`.
   - Explicit `account_type`: `USER_WALLET`, `PLATFORM_CASH`, `PLATFORM_REVENUE`, `ESCROW`, `COMMISSION`.
   - Scoped to `owner_id` (e.g. Telegram User ID or system identifier).
   - Tracks `currency` matching the tenant.
   - Maintains derived `available_balance` and `held_balance` updated only via atomic transactions.
3. **Pessimistic Concurrency & Negative Balance Protection**:
   - Every balance-modifying service wraps execution in `transaction.atomic()`.
   - Acquires `select_for_update()` on the participating `FinancialAccount` rows.
   - Enforces `available_balance >= amount` under lock, preventing race-condition overdrafts.
4. **Idempotency**:
   - `LedgerTransaction` enforces a composite unique constraint: `(tenant, idempotency_key)`.
   - Resubmitted requests return the existing transaction without duplicate financial effects.
5. **Reconciliation**:
   - A dedicated reconciliation engine calculates `sum(CREDITS) - sum(DEBITS)` directly from the ledger and verifies consistency against `available_balance`.
   - Discrepancies are reported without destructive silent modifications.

---

## 6. Explicitly Deferred Functionality
The following items are intentionally excluded from Phase 06:
1. **Live Payment Gateway Integrations**: Stripe, PayPal, ToyyibPay, Razorpay, and direct bank settlement.
2. **Real-world Fiat Payouts / Bank Withdrawals**: Outbound bank rails are deferred.
3. **Foreign Exchange (FX) Conversion**: Cross-currency settlement is out of scope.
4. **Automated Bidding Escrow Lock**: Phase 05 concurrent bidding continues operating without mandatory upfront escrow until business rules for deposit requirements are finalized.
5. **Dashboard UI**: Management interfaces for wallets belong to Phase 07.

---

## 7. Mapping Between Existing Auction Behavior and Future Financial Transactions

| Auction Event | Future Financial Event | Double-Entry Accounting Pattern |
| :--- | :--- | :--- |
| **Seller Lists Item** | Listing Fee | Debit `USER_WALLET` (Seller), Credit `PLATFORM_REVENUE` |
| **Buyer Places Bid** | Bid Escrow Hold (Future) | Hold funds on `USER_WALLET` (transfer available -> held) |
| **Bidder Outbid** | Bid Escrow Release (Future) | Release hold on `USER_WALLET` (transfer held -> available) |
| **Auction Sold** | Hammer Settlement | Debit `USER_WALLET` (Buyer), Credit `USER_WALLET` (Seller), Debit `COMMISSION` |
| **Auction Cancelled** | Fee Refund | Debit `PLATFORM_REVENUE`, Credit `USER_WALLET` (Seller) |
| **Seller Deposit** | External Top-up | Debit `PLATFORM_CASH`, Credit `USER_WALLET` (Seller) |
