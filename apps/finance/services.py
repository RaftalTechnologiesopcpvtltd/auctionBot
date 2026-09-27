from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from django.db import transaction, IntegrityError
from django.utils import timezone
from apps.tenants.models import Tenant
from apps.finance.models import (
    FinancialAccount,
    LedgerTransaction,
    LedgerEntry,
    AccountType,
    AccountStatus,
    TransactionType,
    TransactionStatus,
    EntryType,
)
from apps.finance.exceptions import (
    FinanceError,
    InsufficientFundsError,
    AccountInactiveError,
    CurrencyMismatchError,
    UnbalancedTransactionError,
    InvalidAmountError,
    IdempotencyConflictError,
    CrossTenantOperationError,
    ImmutableLedgerError,
    InvalidTransactionStateError,
    ReconciliationMismatchError,
)


def _to_decimal(val) -> Decimal:
    """Helper to ensure monetary amount is a strict Decimal with 2 decimal places."""
    if isinstance(val, float):
        raise InvalidAmountError("Float values are prohibited for financial calculations.")
    try:
        dec = Decimal(str(val))
    except Exception as e:
        raise InvalidAmountError(f"Invalid monetary value '{val}': {e}")
    if dec <= Decimal("0.00"):
        raise InvalidAmountError(f"Monetary amounts must be strictly positive. Got: {dec}")
    return dec.quantize(Decimal("0.01"))


def create_account(
    tenant: Tenant,
    owner_id: str,
    account_type: str = AccountType.USER_WALLET,
    currency: str = "INR",
) -> FinancialAccount:
    """Create a new financial account for a tenant."""
    currency = currency.strip().upper()
    if len(currency) != 3:
        raise CurrencyMismatchError(f"Invalid currency code: {currency}")

    return FinancialAccount.objects.create(
        tenant=tenant,
        owner_id=str(owner_id).strip(),
        account_type=account_type,
        currency=currency,
        status=AccountStatus.ACTIVE,
        available_balance=Decimal("0.00"),
        held_balance=Decimal("0.00"),
    )


def get_or_create_account(
    tenant: Tenant,
    owner_id: str,
    account_type: str = AccountType.USER_WALLET,
    currency: str = "INR",
) -> Tuple[FinancialAccount, bool]:
    """Retrieve or create a financial account safely."""
    currency = currency.strip().upper()
    owner_id_str = str(owner_id).strip()
    return FinancialAccount.objects.get_or_create(
        tenant=tenant,
        owner_id=owner_id_str,
        account_type=account_type,
        currency=currency,
        defaults={
            "status": AccountStatus.ACTIVE,
            "available_balance": Decimal("0.00"),
            "held_balance": Decimal("0.00"),
        },
    )


def get_system_account(
    tenant: Tenant,
    account_type: str,
    currency: str = "INR",
) -> FinancialAccount:
    """Retrieve or create a tenant-scoped system operational account (e.g. PLATFORM_CASH, ESCROW)."""
    acc, _ = get_or_create_account(
        tenant=tenant,
        owner_id="SYSTEM",
        account_type=account_type,
        currency=currency,
    )
    return acc


def record_transaction(
    tenant: Tenant,
    transaction_type: str,
    entries_data: List[Dict[str, Any]],
    idempotency_key: Optional[str] = None,
    reference_type: str = "",
    reference_id: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Execute an atomic double-entry financial transaction.
    
    Invariants enforced:
    1. Tenant isolation: every account must belong to the given tenant.
    2. Positive amounts: every entry amount must be strictly > 0.
    3. Currency consistency: every entry currency must match account currency and match each other.
    4. Balanced double-entry: sum of DEBITS == sum of CREDITS.
    5. Row locking: accounts are locked using select_for_update() in deterministic sorted ID order.
    6. Negative balance protection: account available_balance cannot drop below zero.
    7. Idempotency: duplicate (tenant, idempotency_key) returns existing transaction.
    """
    if metadata is None:
        metadata = {}

    # Check idempotency first before locking
    if idempotency_key:
        idempotency_key = str(idempotency_key).strip()
        existing = LedgerTransaction.objects.filter(
            tenant=tenant,
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing

    if not entries_data:
        raise UnbalancedTransactionError("Transaction must contain at least one balanced entry pair.")

    # Validate entry inputs
    parsed_entries = []
    account_ids = set()
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")
    expected_currency = None

    for entry in entries_data:
        acc = entry["account"]
        entry_type = entry["entry_type"]
        amount = _to_decimal(entry["amount"])

        if acc.tenant_id != tenant.id:
            raise CrossTenantOperationError(
                f"Account {acc.pk} belongs to tenant {acc.tenant_id}, not transaction tenant {tenant.id}."
            )

        if expected_currency is None:
            expected_currency = acc.currency
        elif acc.currency != expected_currency:
            raise CurrencyMismatchError(
                f"Multi-currency transaction detected: {acc.currency} vs {expected_currency}."
            )

        if entry_type == EntryType.DEBIT:
            total_debits += amount
        elif entry_type == EntryType.CREDIT:
            total_credits += amount
        else:
            raise FinanceError(f"Invalid entry type: {entry_type}")

        account_ids.add(acc.id)
        parsed_entries.append({
            "account_id": acc.id,
            "entry_type": entry_type,
            "amount": amount,
            "currency": acc.currency,
            "metadata": entry.get("metadata", {}),
        })

    # Double-entry invariant
    if total_debits != total_credits:
        raise UnbalancedTransactionError(
            f"Unbalanced transaction: total debits ({total_debits}) != total credits ({total_credits})."
        )

    # Begin atomic transaction with row-level locking
    try:
        with transaction.atomic():
            # Check idempotency again inside transaction to prevent race conditions
            if idempotency_key:
                existing = LedgerTransaction.objects.select_for_update().filter(
                    tenant=tenant,
                    idempotency_key=idempotency_key
                ).first()
                if existing:
                    return existing

            # Deterministic locking order by account ID to prevent deadlocks
            sorted_account_ids = sorted(list(account_ids))
            locked_accounts_qs = FinancialAccount.objects.select_for_update().filter(
                id__in=sorted_account_ids,
                tenant=tenant
            )
            locked_accounts = {acc.id: acc for acc in locked_accounts_qs}

            if len(locked_accounts) != len(sorted_account_ids):
                raise FinanceError("One or more financial accounts could not be locked or found.")

            # Calculate net changes per account
            # Accounting normal balance rules:
            # USER_WALLET, ESCROW, COMMISSION, PLATFORM_REVENUE: Credit increases balance, Debit decreases balance.
            # PLATFORM_CASH: Debit increases cash/asset, Credit decreases cash/asset.
            account_balance_deltas: Dict[int, Decimal] = {aid: Decimal("0.00") for aid in sorted_account_ids}

            for item in parsed_entries:
                aid = item["account_id"]
                acc = locked_accounts[aid]
                amt = item["amount"]
                etype = item["entry_type"]

                if acc.status != AccountStatus.ACTIVE:
                    raise AccountInactiveError(f"Account {acc.pk} ({acc.owner_id}) is {acc.status}.")

                if acc.account_type == AccountType.PLATFORM_CASH:
                    if etype == EntryType.DEBIT:
                        account_balance_deltas[aid] += amt
                    else:
                        account_balance_deltas[aid] -= amt
                else:
                    if etype == EntryType.CREDIT:
                        account_balance_deltas[aid] += amt
                    else:
                        account_balance_deltas[aid] -= amt

            # Validate negative balance protection
            for aid, delta in account_balance_deltas.items():
                acc = locked_accounts[aid]
                new_available = acc.available_balance + delta
                # User wallets and escrow cannot overdraw
                if acc.account_type in (AccountType.USER_WALLET, AccountType.ESCROW) and new_available < Decimal("0.00"):
                    raise InsufficientFundsError(
                        f"Insufficient funds for account {acc.owner_id}. "
                        f"Current: {acc.available_balance}, Delta: {delta}, Result: {new_available}."
                    )

            # Create LedgerTransaction
            ledger_tx = LedgerTransaction.objects.create(
                tenant=tenant,
                transaction_type=transaction_type,
                status=TransactionStatus.COMPLETED,
                reference_type=reference_type,
                reference_id=str(reference_id),
                idempotency_key=idempotency_key,
                description=description,
                completed_at=timezone.now(),
                metadata=metadata,
            )

            # Create LedgerEntries
            for item in parsed_entries:
                LedgerEntry.objects.create(
                    transaction=ledger_tx,
                    account=locked_accounts[item["account_id"]],
                    entry_type=item["entry_type"],
                    amount=item["amount"],
                    currency=item["currency"],
                    metadata=item["metadata"],
                )

            # Update materialized balances
            for aid, delta in account_balance_deltas.items():
                acc = locked_accounts[aid]
                acc.available_balance += delta
                acc.save(update_fields=["available_balance", "updated_at"])

            return ledger_tx
    except IntegrityError as exc:
        if idempotency_key and "unique_tenant_idempotency_key" in str(exc):
            existing = LedgerTransaction.objects.filter(
                tenant=tenant,
                idempotency_key=idempotency_key
            ).first()
            if existing:
                return existing
        raise


def deposit(
    tenant: Tenant,
    user_id: str,
    amount: Any,
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    description: str = "Wallet deposit",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    User deposits money into their wallet.
    Double-entry effect:
    - PLATFORM_CASH: DEBIT amount (Cash asset increases)
    - USER_WALLET: CREDIT amount (User balance increases)
    """
    amount = _to_decimal(amount)
    user_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    cash_account = get_system_account(
        tenant=tenant,
        account_type=AccountType.PLATFORM_CASH,
        currency=currency,
    )

    entries = [
        {"account": cash_account, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": user_wallet, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    return record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.DEPOSIT,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type="DEPOSIT",
        reference_id=user_id,
        description=description,
        metadata=metadata,
    )


def debit_account(
    tenant: Tenant,
    user_id: str,
    amount: Any,
    destination_type: str = AccountType.PLATFORM_REVENUE,
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    reference_type: str = "",
    reference_id: str = "",
    description: str = "Wallet debit",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Debit funds from a user's wallet towards platform revenue/fees.
    Double-entry effect:
    - USER_WALLET: DEBIT amount (User balance decreases)
    - DESTINATION: CREDIT amount (Platform revenue/fee increases)
    """
    amount = _to_decimal(amount)
    user_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    dest_account = get_system_account(
        tenant=tenant,
        account_type=destination_type,
        currency=currency,
    )

    entries = [
        {"account": user_wallet, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": dest_account, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    return record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.PURCHASE if destination_type == AccountType.PLATFORM_REVENUE else TransactionType.FEE,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        metadata=metadata,
    )


def transfer(
    tenant: Tenant,
    from_user_id: str,
    to_user_id: str,
    amount: Any,
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    reference_type: str = "",
    reference_id: str = "",
    description: str = "Transfer",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Transfer funds between two user wallets within the same tenant.
    Double-entry effect:
    - Sender USER_WALLET: DEBIT amount
    - Recipient USER_WALLET: CREDIT amount
    """
    amount = _to_decimal(amount)
    sender_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=from_user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    recipient_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=to_user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )

    entries = [
        {"account": sender_wallet, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": recipient_wallet, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    return record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.TRANSFER,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        metadata=metadata,
    )


def hold_funds(
    tenant: Tenant,
    user_id: str,
    amount: Any,
    reference_type: str = "AUCTION_BID",
    reference_id: str = "",
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    description: str = "Hold funds for auction bid",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Reserves funds for an active auction bid.
    Double-entry effect:
    - USER_WALLET: DEBIT amount (reduces available_balance)
    - ESCROW: CREDIT amount (held in escrow account)
    Also updates held_balance on USER_WALLET for fast visibility.
    """
    amount = _to_decimal(amount)
    user_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    escrow_account = get_system_account(
        tenant=tenant,
        account_type=AccountType.ESCROW,
        currency=currency,
    )

    entries = [
        {"account": user_wallet, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": escrow_account, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    tx = record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.BID_HOLD,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        metadata=metadata,
    )

    # Reflect reservation in held_balance
    with transaction.atomic():
        user_wallet = FinancialAccount.objects.select_for_update().get(id=user_wallet.id)
        user_wallet.held_balance += amount
        user_wallet.save(update_fields=["held_balance", "updated_at"])

    return tx


def release_hold(
    tenant: Tenant,
    user_id: str,
    amount: Any,
    reference_type: str = "AUCTION_BID",
    reference_id: str = "",
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    description: str = "Release held bid funds",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Releases previously held bid funds back to user's available balance (e.g. outbid).
    Double-entry effect:
    - ESCROW: DEBIT amount (funds leave escrow)
    - USER_WALLET: CREDIT amount (funds return to user wallet)
    Updates held_balance on USER_WALLET.
    """
    amount = _to_decimal(amount)
    user_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    escrow_account = get_system_account(
        tenant=tenant,
        account_type=AccountType.ESCROW,
        currency=currency,
    )

    entries = [
        {"account": escrow_account, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": user_wallet, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    tx = record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.BID_RELEASE,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        metadata=metadata,
    )

    # Deduct from held_balance
    with transaction.atomic():
        user_wallet = FinancialAccount.objects.select_for_update().get(id=user_wallet.id)
        new_held = user_wallet.held_balance - amount
        user_wallet.held_balance = max(Decimal("0.00"), new_held)
        user_wallet.save(update_fields=["held_balance", "updated_at"])

    return tx


def capture_hold(
    tenant: Tenant,
    user_id: str,
    amount: Any,
    destination_account: FinancialAccount,
    reference_type: str = "AUCTION_SETTLEMENT",
    reference_id: str = "",
    currency: str = "INR",
    idempotency_key: Optional[str] = None,
    description: str = "Capture held funds on auction win",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Captures previously held funds from Escrow to a destination account (seller wallet or platform).
    Double-entry effect:
    - ESCROW: DEBIT amount
    - DESTINATION: CREDIT amount
    Also clears held_balance on user wallet.
    """
    amount = _to_decimal(amount)
    user_wallet, _ = get_or_create_account(
        tenant=tenant,
        owner_id=user_id,
        account_type=AccountType.USER_WALLET,
        currency=currency,
    )
    escrow_account = get_system_account(
        tenant=tenant,
        account_type=AccountType.ESCROW,
        currency=currency,
    )

    entries = [
        {"account": escrow_account, "entry_type": EntryType.DEBIT, "amount": amount},
        {"account": destination_account, "entry_type": EntryType.CREDIT, "amount": amount},
    ]

    tx = record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.PURCHASE,
        entries_data=entries,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        metadata=metadata,
    )

    with transaction.atomic():
        user_wallet = FinancialAccount.objects.select_for_update().get(id=user_wallet.id)
        new_held = user_wallet.held_balance - amount
        user_wallet.held_balance = max(Decimal("0.00"), new_held)
        user_wallet.save(update_fields=["held_balance", "updated_at"])

    return tx


def refund(
    tenant: Tenant,
    original_transaction: LedgerTransaction,
    idempotency_key: Optional[str] = None,
    description: str = "Transaction refund",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Refund a completed transaction by creating an exact reversing transaction.
    The original transaction is preserved unchanged.
    """
    if original_transaction.tenant_id != tenant.id:
        raise CrossTenantOperationError("Cannot refund transaction belonging to another tenant.")

    if original_transaction.status != TransactionStatus.COMPLETED:
        raise InvalidTransactionStateError(
            f"Cannot refund transaction in status {original_transaction.status}."
        )

    # Invert entries
    reversing_entries = []
    for entry in original_transaction.entries.all():
        inverted_type = EntryType.CREDIT if entry.entry_type == EntryType.DEBIT else EntryType.DEBIT
        reversing_entries.append({
            "account": entry.account,
            "entry_type": inverted_type,
            "amount": entry.amount,
            "metadata": {"reverses_entry_id": entry.id},
        })

    tx_meta = {
        "original_transaction_id": original_transaction.id,
        **(metadata or {})
    }

    return record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.REFUND,
        entries_data=reversing_entries,
        idempotency_key=idempotency_key,
        reference_type="REFUND",
        reference_id=str(original_transaction.id),
        description=description,
        metadata=tx_meta,
    )


def reverse_transaction(
    tenant: Tenant,
    original_transaction: LedgerTransaction,
    idempotency_key: Optional[str] = None,
    description: str = "Transaction reversal",
    metadata: Optional[Dict[str, Any]] = None,
) -> LedgerTransaction:
    """
    Reverse an erroneous transaction by creating compensating entries and marking
    the original transaction as REVERSED.
    Original entries remain completely immutable.
    """
    if original_transaction.tenant_id != tenant.id:
        raise CrossTenantOperationError("Cannot reverse transaction belonging to another tenant.")

    if original_transaction.status != TransactionStatus.COMPLETED:
        raise InvalidTransactionStateError(
            f"Cannot reverse transaction in status {original_transaction.status}."
        )

    reversing_entries = []
    for entry in original_transaction.entries.all():
        inverted_type = EntryType.CREDIT if entry.entry_type == EntryType.DEBIT else EntryType.DEBIT
        reversing_entries.append({
            "account": entry.account,
            "entry_type": inverted_type,
            "amount": entry.amount,
            "metadata": {"reverses_entry_id": entry.id},
        })

    tx_meta = {
        "reverses_transaction_id": original_transaction.id,
        **(metadata or {})
    }

    rev_tx = record_transaction(
        tenant=tenant,
        transaction_type=TransactionType.REVERSAL,
        entries_data=reversing_entries,
        idempotency_key=idempotency_key,
        reference_type="REVERSAL",
        reference_id=str(original_transaction.id),
        description=description,
        metadata=tx_meta,
    )

    # Mark original transaction as REVERSED without modifying historical entries
    with transaction.atomic():
        orig_locked = LedgerTransaction.objects.select_for_update().get(id=original_transaction.id)
        orig_locked.status = TransactionStatus.REVERSED
        orig_locked.save(update_fields=["status"])

    return rev_tx


def reconcile_account(account: FinancialAccount, repair: bool = False) -> Dict[str, Any]:
    """
    Calculates expected account balance directly from the immutable ledger entries
    and compares it against the stored materialized balance.
    
    If repair=True, explicitly corrects the stored balance to match the ledger.
    """
    entries = LedgerEntry.objects.filter(account=account)
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")

    for entry in entries:
        if entry.entry_type == EntryType.DEBIT:
            total_debits += entry.amount
        elif entry.entry_type == EntryType.CREDIT:
            total_credits += entry.amount

    # Compute expected balance based on account type
    if account.account_type == AccountType.PLATFORM_CASH:
        expected_balance = total_debits - total_credits
    else:
        # User wallets, revenue, escrow, commission
        expected_balance = total_credits - total_debits

    difference = account.available_balance - expected_balance
    is_reconciled = (difference == Decimal("0.00"))
    was_repaired = False
    original_stored = account.available_balance

    if repair and not is_reconciled:
        with transaction.atomic():
            locked = FinancialAccount.objects.select_for_update().get(id=account.id)
            locked.available_balance = expected_balance
            locked.save(update_fields=["available_balance", "updated_at"])
            account.available_balance = expected_balance
            was_repaired = True
            is_reconciled = True

    return {
        "tenant": account.tenant.slug,
        "account_id": account.id,
        "owner_id": account.owner_id,
        "account_type": account.account_type,
        "currency": account.currency,
        "stored_available": original_stored,
        "stored_held": account.held_balance,
        "expected_balance": expected_balance,
        "difference": difference,
        "is_reconciled": is_reconciled,
        "was_repaired": was_repaired,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "entry_count": entries.count(),
    }
