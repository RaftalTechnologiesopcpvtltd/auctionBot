# Multi-Tenant Financial Architecture & Double-Entry Ledger

## 1. Overview & Core Philosophy

The financial subsystem of the unified auction platform is built on an inviolable foundation:

> **THE LEDGER IS THE SINGLE AUTHORITATIVE SOURCE OF TRUTH.**

Mutable balances (e.g. `wallet.balance += 100`) are strictly prohibited as primary financial records. Every movement of funds is captured as an atomic, immutable, double-entry financial transaction (`LedgerTransaction`) consisting of balanced credit and debit ledger entries (`LedgerEntry`).

Materialized balance columns (`available_balance` and `held_balance`) on `FinancialAccount` are derived snapshots maintained exclusively within the database transaction boundary under row locks, and can always be verified or recomputed from historical ledger entries.

---

## 2. Monetary Precision & Currency Representation

- Floating-point arithmetic (`float`) is strictly prohibited.
- All monetary amounts are stored and processed as exact Python `Decimal` objects quantized to 2 decimal places (`max_digits=14, decimal_places=2`).
- Every `FinancialAccount` and `LedgerEntry` defines an explicit ISO 4217 currency code (e.g., `INR`, `USD`, `MYR`, `AUD`).
- Transactions are single-currency by definition; cross-currency transactions within an individual ledger entry set are rejected with `CurrencyMismatchError`. Multi-currency conversions/settlements are deferred.

---

## 3. Financial Domain Models

The architecture resides in `apps/finance/`:

```
apps/finance/
├── admin.py
├── apps.py
├── exceptions.py
├── management/commands/reconcile_financial_accounts.py
├── migrations/
├── models.py
├── services.py
└── tests.py
```

### 3.1 FinancialAccount
- Inherits from `TenantOwnedModel` to enforce strict database-level tenant isolation.
- Fields:
  - `tenant`: Foreign key to `Tenant` (CASCADE).
  - `owner_id`: Tenant-scoped identifier (e.g., Telegram user ID, internal user ID, or system tag `SYSTEM`).
  - `account_type`: `USER_WALLET`, `PLATFORM_CASH`, `PLATFORM_REVENUE`, `ESCROW`, `COMMISSION`.
  - `currency`: ISO 4217 3-character code (default: `INR`).
  - `status`: `ACTIVE`, `SUSPENDED`, `CLOSED`.
  - `available_balance`: Materialized available funds.
  - `held_balance`: Materialized reserved/escrowed funds (for active bids).
- Constraints:
  - Unique composite constraint: `(tenant, owner_id, account_type, currency)`.
- Deletion Protection:
  - Overrides `delete()` to raise `ImmutableLedgerError` if any ledger entries reference the account.

### 3.2 LedgerTransaction
- Represents an atomic, discrete financial event.
- Belongs strictly to exactly one tenant (`TenantOwnedModel`).
- Fields:
  - `tenant`: Foreign key to `Tenant`.
  - `transaction_type`: `DEPOSIT`, `WITHDRAWAL`, `BID_HOLD`, `BID_RELEASE`, `PURCHASE`, `REFUND`, `COMMISSION`, `FEE`, `ADJUSTMENT`, `REVERSAL`, `TRANSFER`.
  - `status`: `PENDING`, `COMPLETED`, `FAILED`, `REVERSED`.
  - `reference_type`: Domain context (e.g., `AUCTION`, `BID`, `DEPOSIT`, `REFUND`).
  - `reference_id`: String ID of related domain entity.
  - `idempotency_key`: Optional caller-provided token for at-most-once execution.
  - `metadata`: JSON payload for non-sensitive audit metadata.
- Constraints:
  - Unique composite constraint: `(tenant, idempotency_key)` where `idempotency_key IS NOT NULL`.
- Immutability:
  - Deletion is prohibited via `ImmutableLedgerError`.

### 3.3 LedgerEntry
- Represents an individual movement of money into or out of an account.
- Fields:
  - `transaction`: Foreign key to `LedgerTransaction` (CASCADE).
  - `account`: Foreign key to `FinancialAccount` (`on_delete=models.PROTECT`).
  - `entry_type`: `DEBIT` or `CREDIT`.
  - `amount`: Strictly positive `Decimal(14, 2)`.
  - `currency`: ISO 4217 code matching the account and transaction.
- Constraints:
  - Database check constraint: `amount > 0.00`.
- Immutability:
  - Overrides `save()` (updates blocked if `pk` exists) and `delete()` to raise `ImmutableLedgerError`.

---

## 4. Double-Entry Accounting Direction & Invariants

### 4.1 Invariants Enforced
1. **Balanced Transaction Invariant**: $\sum \text{Debits} == \sum \text{Credits}$. Unbalanced entries raise `UnbalancedTransactionError`.
2. **Positive Amounts**: Every entry amount $> 0.00$. Sign is dictated solely by `entry_type`.
3. **Tenant Consistency**: All accounts participating in a transaction must belong to the same `tenant`. Cross-tenant transactions raise `CrossTenantOperationError`.
4. **Currency Uniformity**: All participating accounts and entries must share the same currency.
5. **Account Normal Balances**:
   - **Assets (`PLATFORM_CASH`)**: Normal balance is DEBIT ($\Delta = \text{Debit} - \text{Credit}$).
   - **Liabilities (`USER_WALLET`, `ESCROW`)**: Normal balance is CREDIT ($\Delta = \text{Credit} - \text{Debit}$).
   - **Revenue/Equity (`PLATFORM_REVENUE`, `COMMISSION`)**: Normal balance is CREDIT ($\Delta = \text{Credit} - \text{Debit}$).

### 4.2 Core Transaction Flows

#### A. User Deposit
User deposits ₹1,000 into their wallet:
- `PLATFORM_CASH` (Asset increases): `DEBIT 1000.00`
- `USER_WALLET` (Liability/User balance increases): `CREDIT 1000.00`
- Net effect: Available balance +₹1,000.00.

#### B. User Purchase / Platform Fee
User pays ₹300 for a service or commission:
- `USER_WALLET`: `DEBIT 300.00`
- `PLATFORM_REVENUE`: `CREDIT 300.00`
- Net effect: User available balance -₹300.00, Platform revenue +₹300.00.

#### C. Auction Bid Hold / Reservation
User with ₹10,000 places a bid requiring ₹3,000 reservation:
- `USER_WALLET`: `DEBIT 3000.00`
- `ESCROW`: `CREDIT 3000.00`
- Balance effect: `available_balance` becomes ₹7,000.00, `held_balance` becomes ₹3,000.00. Total balance remains ₹10,000.00.

#### D. Auction Outbid (Release Hold)
User is outbid on auction; hold is returned:
- `ESCROW`: `DEBIT 3000.00`
- `USER_WALLET`: `CREDIT 3000.00`
- Balance effect: `available_balance` returns to ₹10,000.00, `held_balance` resets to ₹0.00.

#### E. Auction Settlement (Capture Hold)
User wins auction; funds are captured to seller wallet:
- `ESCROW`: `DEBIT 3000.00`
- `SELLER_WALLET`: `CREDIT 3000.00`
- Balance effect: Buyer `held_balance` cleared; Seller `available_balance` +₹3,000.00.

#### F. Refund
Reverses the entries of a previous transaction in a new `REFUND` transaction:
- Original transaction remains untouched and completed.
- Offsetting entries are created.

#### G. Reversal
Creates compensating entries in a new `REVERSAL` transaction and updates original status to `REVERSED`.
- Ledger historical entries remain completely immutable.

---

## 5. Concurrency Control & Row Locking

- Financial transactions must execute inside `transaction.atomic()`.
- Participating account rows are locked using PostgreSQL `select_for_update()`.
- **Deadlock Prevention**: Account rows are locked in a deterministic, sorted order by `id` (`sorted(list(account_ids))`).
- **Negative Balance Protection**: Under row lock, the available balance is checked. If net debit would cause `available_balance < 0.00`, `InsufficientFundsError` is raised and the transaction is rolled back immediately.
- **Race Condition Idempotency**: If two threads race with identical `idempotency_key`, database unique constraint violations (`IntegrityError`) are caught and the winning transaction is retrieved and returned safely.
- **Zero External Network Calls**: Payment gateways, Telegram API, or HTTP services are NEVER called within the financial database transaction boundary.

---

## 6. Reconciliation Engine

The system provides `reconcile_account()` and a management command:

```bash
# Report-only audit (Default)
python manage.py reconcile_financial_accounts

# Filter by tenant
python manage.py reconcile_financial_accounts --tenant cyg-my

# Explicit repair mode
python manage.py reconcile_financial_accounts --repair
```

Reconciliation sums all `LedgerEntry` rows for an account according to its normal balance type and compares it against `available_balance`. Any discrepancy is flagged. In repair mode, the materialized balance is corrected to match ledger truth.

---

## 7. Deferred Functionality
- Live Payment Gateways (Stripe, PayPal, Razorpay, FPX).
- Real-world bank payouts / withdrawals.
- Multi-currency live FX conversion and cross-currency settlement.
- Web dashboard wallet UI (Phase 07).
