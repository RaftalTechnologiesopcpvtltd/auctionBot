from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.tenants.models import TenantOwnedModel
from apps.finance.exceptions import ImmutableLedgerError, InvalidAmountError


class AccountType(models.TextChoices):
    USER_WALLET = "USER_WALLET", "User Wallet"
    PLATFORM_CASH = "PLATFORM_CASH", "Platform Cash Account"
    PLATFORM_REVENUE = "PLATFORM_REVENUE", "Platform Revenue Account"
    ESCROW = "ESCROW", "Escrow Account"
    COMMISSION = "COMMISSION", "Commission Account"


class AccountStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    CLOSED = "CLOSED", "Closed"


class TransactionType(models.TextChoices):
    DEPOSIT = "DEPOSIT", "Deposit"
    WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
    BID_HOLD = "BID_HOLD", "Bid Hold"
    BID_RELEASE = "BID_RELEASE", "Bid Release"
    PURCHASE = "PURCHASE", "Purchase"
    REFUND = "REFUND", "Refund"
    COMMISSION = "COMMISSION", "Commission"
    FEE = "FEE", "Fee"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"
    REVERSAL = "REVERSAL", "Reversal"
    TRANSFER = "TRANSFER", "Transfer"


class TransactionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    REVERSED = "REVERSED", "Reversed"


class EntryType(models.TextChoices):
    DEBIT = "DEBIT", "Debit"
    CREDIT = "CREDIT", "Credit"


class FinancialAccount(TenantOwnedModel):
    """
    Represents a financial account owned by a tenant.
    Can be a user's wallet, platform operational account, escrow, or commission account.
    
    Materialized balances:
    - available_balance: funds freely available for debit or hold.
    - held_balance: funds reserved for active bids or escrow operations.
    The ledger entries are the authoritative source of truth.
    """
    owner_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Tenant-scoped identifier for owner (e.g. Telegram ID or SYSTEM)"
    )
    account_type = models.CharField(
        max_length=32,
        choices=AccountType.choices,
        default=AccountType.USER_WALLET,
        db_index=True
    )
    currency = models.CharField(
        max_length=3,
        default="INR",
        db_index=True,
        help_text="ISO 4217 Currency Code"
    )
    status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True
    )
    available_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Materialized available balance. Authoritative truth derives from ledger."
    )
    held_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Materialized held/reserved balance."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "owner_id", "account_type", "currency"],
                name="unique_tenant_owner_account_currency"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "owner_id"]),
            models.Index(fields=["tenant", "account_type"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.slug}:{self.account_type}:{self.owner_id} ({self.currency} {self.available_balance})"

    @property
    def total_balance(self) -> Decimal:
        return self.available_balance + self.held_balance

    def delete(self, *args, **kwargs):
        if self.ledger_entries.exists():
            raise ImmutableLedgerError(
                f"Cannot delete financial account {self.pk} with existing ledger history."
            )
        return super().delete(*args, **kwargs)


class LedgerTransaction(TenantOwnedModel):
    """
    Represents an atomic financial event containing double-entry ledger records.
    Every financial transaction belongs to exactly one tenant.
    """
    transaction_type = models.CharField(
        max_length=32,
        choices=TransactionType.choices,
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.COMPLETED,
        db_index=True
    )
    reference_type = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Domain entity type (e.g. AUCTION, BID, DEPOSIT)"
    )
    reference_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Domain entity identifier"
    )
    idempotency_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text="Unique caller-supplied token to prevent duplicate operations"
    )
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_tenant_idempotency_key"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "reference_type", "reference_id"]),
            models.Index(fields=["tenant", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Txn #{self.pk} [{self.tenant.slug}] {self.transaction_type} ({self.status})"

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError(
            f"Cannot delete financial transaction {self.pk}: ledger history is immutable."
        )


class LedgerEntry(models.Model):
    """
    Immutable single-entry record in the double-entry accounting ledger.
    Every entry represents money moving into (CREDIT) or out of (DEBIT) an account.
    Amounts must always be positive Decimal values.
    """
    transaction = models.ForeignKey(
        LedgerTransaction,
        on_delete=models.CASCADE,
        related_name="entries"
    )
    account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="ledger_entries"
    )
    entry_type = models.CharField(
        max_length=10,
        choices=EntryType.choices
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Strictly positive monetary amount."
    )
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["account", "created_at"]),
            models.Index(fields=["transaction", "entry_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=Decimal("0.00")),
                name="positive_ledger_entry_amount"
            )
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"Entry #{self.pk}: {self.account.owner_id} {self.entry_type} {self.currency} {self.amount}"

    def clean(self):
        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise InvalidAmountError("Ledger entry amount must be strictly positive.")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableLedgerError(
                f"Cannot update historical ledger entry {self.pk}: entries are strictly immutable."
            )
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError(
            f"Cannot delete ledger entry {self.pk}: ledger records are immutable."
        )


class DepositStatus(models.TextChoices):
    PENDING = "PENDING", "Pending Approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class DepositProofRequest(TenantOwnedModel):
    """
    Tracks seller deposit submissions through Telegram before admin approval.
    When approved by client/admin:
    - Deducts from Tenant Client pool / Credits Seller Wallet.
    - Emits immutable double-entry ledger records.
    """
    telegram_user_id = models.CharField(max_length=128, db_index=True)
    telegram_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="MYR")
    proof_image = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=DepositStatus.choices,
        default=DepositStatus.PENDING,
        db_index=True
    )
    admin_notes = models.CharField(max_length=255, blank=True, default="")
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "telegram_user_id"]),
            models.Index(fields=["tenant", "created_at"]),
        ]

    def __str__(self):
        return f"Deposit #{self.pk} - {self.telegram_user_id} - {self.currency} {self.amount} ({self.status})"
