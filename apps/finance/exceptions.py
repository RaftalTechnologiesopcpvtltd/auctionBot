"""Domain-specific financial exceptions."""


class FinanceError(Exception):
    """Base exception for all finance domain errors."""
    pass


class InsufficientFundsError(FinanceError):
    """Raised when an account does not have sufficient available balance for a debit/hold."""
    pass


class AccountInactiveError(FinanceError):
    """Raised when attempting an operation on an inactive, suspended, or closed account."""
    pass


class CurrencyMismatchError(FinanceError):
    """Raised when attempting a transaction or entry with mismatched currencies."""
    pass


class UnbalancedTransactionError(FinanceError):
    """Raised when total debits != total credits in a balanced ledger transaction."""
    pass


class InvalidAmountError(FinanceError):
    """Raised when a monetary amount is zero, negative, or non-decimal."""
    pass


class IdempotencyConflictError(FinanceError):
    """Raised when an operation with the same idempotency key is submitted with differing parameters."""
    pass


class CrossTenantOperationError(FinanceError):
    """Raised when attempting a financial operation crossing tenant boundaries."""
    pass


class ImmutableLedgerError(FinanceError):
    """Raised when an attempt is made to mutate or delete committed ledger records."""
    pass


class InvalidTransactionStateError(FinanceError):
    """Raised when a transaction state transition is illegal."""
    pass


class ReconciliationMismatchError(FinanceError):
    """Raised when ledger-derived balance differs from materialized account balance."""
    pass
