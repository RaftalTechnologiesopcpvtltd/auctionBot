import threading
from decimal import Decimal
from django.db import connection, IntegrityError
from django.test import TestCase, TransactionTestCase
from django.core.management import call_command
from io import StringIO
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
from apps.finance.services import (
    create_account,
    get_or_create_account,
    get_system_account,
    record_transaction,
    deposit,
    debit_account,
    transfer,
    hold_funds,
    release_hold,
    capture_hold,
    refund,
    reverse_transaction,
    reconcile_account,
)
from apps.finance.exceptions import (
    FinanceError,
    InsufficientFundsError,
    AccountInactiveError,
    CurrencyMismatchError,
    UnbalancedTransactionError,
    InvalidAmountError,
    CrossTenantOperationError,
    ImmutableLedgerError,
    InvalidTransactionStateError,
)


class FinanceModelTests(TestCase):
    """Unit tests for finance data models and database-level constraints."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Finance Tenant A",
            slug="fin-tenant-a",
            code="FA",
            country="India",
            timezone="Asia/Kolkata",
            currency="INR",
        )

    def test_account_creation_and_defaults(self):
        """FinancialAccount creates with correct defaults and fields."""
        acc = create_account(self.tenant, owner_id="user_123")
        self.assertEqual(acc.tenant, self.tenant)
        self.assertEqual(acc.owner_id, "user_123")
        self.assertEqual(acc.account_type, AccountType.USER_WALLET)
        self.assertEqual(acc.currency, "INR")
        self.assertEqual(acc.status, AccountStatus.ACTIVE)
        self.assertEqual(acc.available_balance, Decimal("0.00"))
        self.assertEqual(acc.held_balance, Decimal("0.00"))
        self.assertEqual(acc.total_balance, Decimal("0.00"))

    def test_unique_tenant_owner_account_currency(self):
        """Account uniqueness constraint prevents duplicate account per owner, type, currency."""
        create_account(self.tenant, owner_id="user_unique", currency="INR")
        with self.assertRaises(IntegrityError):
            create_account(self.tenant, owner_id="user_unique", currency="INR")

    def test_protected_account_deletion_with_ledger_entries(self):
        """Accounts with existing ledger history cannot be deleted."""
        deposit(self.tenant, user_id="user_protected", amount=Decimal("100.00"))
        acc = FinancialAccount.objects.get(tenant=self.tenant, owner_id="user_protected")
        with self.assertRaises(ImmutableLedgerError):
            acc.delete()

    def test_ledger_transaction_immutability(self):
        """Committed ledger transactions cannot be deleted."""
        tx = deposit(self.tenant, user_id="user_tx", amount=Decimal("50.00"))
        with self.assertRaises(ImmutableLedgerError):
            tx.delete()

    def test_ledger_entry_immutability(self):
        """Committed ledger entries cannot be updated or deleted."""
        tx = deposit(self.tenant, user_id="user_entry", amount=Decimal("50.00"))
        entry = tx.entries.first()

        with self.assertRaises(ImmutableLedgerError):
            entry.amount = Decimal("999.00")
            entry.save()

        with self.assertRaises(ImmutableLedgerError):
            entry.delete()

    def test_positive_ledger_entry_amount(self):
        """Ledger entry amounts must be strictly positive."""
        acc = create_account(self.tenant, owner_id="user_neg")
        tx = LedgerTransaction.objects.create(
            tenant=self.tenant,
            transaction_type=TransactionType.DEPOSIT,
        )
        with self.assertRaises(InvalidAmountError):
            LedgerEntry.objects.create(
                transaction=tx,
                account=acc,
                entry_type=EntryType.CREDIT,
                amount=Decimal("-10.00"),
                currency="INR",
            )


class FinanceServiceTests(TestCase):
    """Unit tests for financial domain service operations and business rules."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="Tenant Alpha",
            slug="alpha-tenant",
            code="TA",
            country="India",
            timezone="Asia/Kolkata",
            currency="INR",
        )
        self.tenant_b = Tenant.objects.create(
            name="Tenant Beta",
            slug="beta-tenant",
            code="TB",
            country="India",
            timezone="Asia/Kolkata",
            currency="INR",
        )

    def test_successful_deposit(self):
        """Deposit credits user wallet and debits platform cash in balanced double-entry."""
        tx = deposit(
            tenant=self.tenant_a,
            user_id="user_dep",
            amount=Decimal("1000.00"),
            currency="INR",
        )
        self.assertEqual(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual(tx.transaction_type, TransactionType.DEPOSIT)
        self.assertEqual(tx.entries.count(), 2)

        user_wallet = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="user_dep")
        cash_account = get_system_account(self.tenant_a, AccountType.PLATFORM_CASH)

        self.assertEqual(user_wallet.available_balance, Decimal("1000.00"))
        self.assertEqual(cash_account.available_balance, Decimal("1000.00"))

    def test_duplicate_deposit_idempotency(self):
        """Submitting deposit with same idempotency key returns existing transaction without double crediting."""
        key = "idemp-deposit-001"
        tx1 = deposit(self.tenant_a, "user_idemp", Decimal("500.00"), idempotency_key=key)
        tx2 = deposit(self.tenant_a, "user_idemp", Decimal("500.00"), idempotency_key=key)

        self.assertEqual(tx1.id, tx2.id)
        user_wallet = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="user_idemp")
        self.assertEqual(user_wallet.available_balance, Decimal("500.00"))
        self.assertEqual(LedgerTransaction.objects.filter(tenant=self.tenant_a, idempotency_key=key).count(), 1)

    def test_invalid_deposit_amounts_rejected(self):
        """Zero, negative, and float amounts are strictly rejected."""
        with self.assertRaises(InvalidAmountError):
            deposit(self.tenant_a, "u1", Decimal("0.00"))

        with self.assertRaises(InvalidAmountError):
            deposit(self.tenant_a, "u1", Decimal("-10.00"))

        with self.assertRaises(InvalidAmountError):
            deposit(self.tenant_a, "u1", 100.50)  # Float rejected

    def test_successful_debit_account(self):
        """Debiting user wallet decreases available balance and credits platform revenue."""
        deposit(self.tenant_a, "user_deb", Decimal("1000.00"))
        tx = debit_account(self.tenant_a, "user_deb", Decimal("300.00"))

        self.assertEqual(tx.status, TransactionStatus.COMPLETED)
        user_wallet = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="user_deb")
        revenue_acc = get_system_account(self.tenant_a, AccountType.PLATFORM_REVENUE)

        self.assertEqual(user_wallet.available_balance, Decimal("700.00"))
        self.assertEqual(revenue_acc.available_balance, Decimal("300.00"))

    def test_debit_insufficient_funds_rejected(self):
        """Debit request exceeding available balance is rejected with InsufficientFundsError."""
        deposit(self.tenant_a, "user_poor", Decimal("100.00"))
        with self.assertRaises(InsufficientFundsError):
            debit_account(self.tenant_a, "user_poor", Decimal("150.00"))

        user_wallet = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="user_poor")
        self.assertEqual(user_wallet.available_balance, Decimal("100.00"))

    def test_transfer_between_users(self):
        """Transfer debits sender and credits recipient within same tenant."""
        deposit(self.tenant_a, "sender", Decimal("500.00"))
        tx = transfer(self.tenant_a, "sender", "recipient", Decimal("200.00"))

        self.assertEqual(tx.status, TransactionStatus.COMPLETED)
        sender_acc = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="sender")
        recip_acc = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="recipient")

        self.assertEqual(sender_acc.available_balance, Decimal("300.00"))
        self.assertEqual(recip_acc.available_balance, Decimal("200.00"))

    def test_hold_release_and_capture_lifecycle(self):
        """Fund reservation lifecycle: hold moves to held_balance, release restores, capture settles."""
        # Initial deposit
        deposit(self.tenant_a, "bidder", Decimal("1000.00"))
        bidder_acc = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="bidder")
        self.assertEqual(bidder_acc.available_balance, Decimal("1000.00"))
        self.assertEqual(bidder_acc.held_balance, Decimal("0.00"))

        # 1. Hold funds for bid
        hold_tx = hold_funds(
            tenant=self.tenant_a,
            user_id="bidder",
            amount=Decimal("300.00"),
            reference_type="BID",
            reference_id="bid_42",
        )
        self.assertEqual(hold_tx.transaction_type, TransactionType.BID_HOLD)
        bidder_acc.refresh_from_db()
        self.assertEqual(bidder_acc.available_balance, Decimal("700.00"))
        self.assertEqual(bidder_acc.held_balance, Decimal("300.00"))
        self.assertEqual(bidder_acc.total_balance, Decimal("1000.00"))

        escrow_acc = get_system_account(self.tenant_a, AccountType.ESCROW)
        self.assertEqual(escrow_acc.available_balance, Decimal("300.00"))

        # 2. Release hold (e.g. user was outbid)
        rel_tx = release_hold(
            tenant=self.tenant_a,
            user_id="bidder",
            amount=Decimal("300.00"),
            reference_type="BID",
            reference_id="bid_42",
        )
        self.assertEqual(rel_tx.transaction_type, TransactionType.BID_RELEASE)
        bidder_acc.refresh_from_db()
        self.assertEqual(bidder_acc.available_balance, Decimal("1000.00"))
        self.assertEqual(bidder_acc.held_balance, Decimal("0.00"))
        escrow_acc.refresh_from_db()
        self.assertEqual(escrow_acc.available_balance, Decimal("0.00"))

        # 3. Re-hold and capture on auction win
        hold_funds(self.tenant_a, "bidder", Decimal("400.00"), reference_id="bid_43")
        seller_acc, _ = get_or_create_account(self.tenant_a, owner_id="seller_fish")

        cap_tx = capture_hold(
            tenant=self.tenant_a,
            user_id="bidder",
            amount=Decimal("400.00"),
            destination_account=seller_acc,
            reference_id="auction_99",
        )
        self.assertEqual(cap_tx.status, TransactionStatus.COMPLETED)

        bidder_acc.refresh_from_db()
        seller_acc.refresh_from_db()
        escrow_acc.refresh_from_db()

        self.assertEqual(bidder_acc.available_balance, Decimal("600.00"))
        self.assertEqual(bidder_acc.held_balance, Decimal("0.00"))
        self.assertEqual(seller_acc.available_balance, Decimal("400.00"))
        self.assertEqual(escrow_acc.available_balance, Decimal("0.00"))

    def test_refund_creates_reversing_transaction_and_preserves_original(self):
        """Refund creates a new transaction with reversed entries, leaving original intact."""
        deposit(self.tenant_a, "refund_user", Decimal("500.00"))
        orig_tx = debit_account(self.tenant_a, "refund_user", Decimal("200.00"))

        user_acc = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="refund_user")
        self.assertEqual(user_acc.available_balance, Decimal("300.00"))

        refund_tx = refund(self.tenant_a, orig_tx, description="Product return refund")
        self.assertEqual(refund_tx.transaction_type, TransactionType.REFUND)
        self.assertEqual(refund_tx.status, TransactionStatus.COMPLETED)

        # Original transaction remains completed and intact
        orig_tx.refresh_from_db()
        self.assertEqual(orig_tx.status, TransactionStatus.COMPLETED)

        # User account restored
        user_acc.refresh_from_db()
        self.assertEqual(user_acc.available_balance, Decimal("500.00"))

    def test_reverse_transaction_marks_original_reversed(self):
        """Reversal creates compensating entries and updates original transaction status to REVERSED."""
        deposit(self.tenant_a, "rev_user", Decimal("400.00"))
        orig_tx = debit_account(self.tenant_a, "rev_user", Decimal("150.00"))

        rev_tx = reverse_transaction(self.tenant_a, orig_tx, description="Operator mistake reversal")
        self.assertEqual(rev_tx.transaction_type, TransactionType.REVERSAL)

        orig_tx.refresh_from_db()
        self.assertEqual(orig_tx.status, TransactionStatus.REVERSED)

        user_acc = FinancialAccount.objects.get(tenant=self.tenant_a, owner_id="rev_user")
        self.assertEqual(user_acc.available_balance, Decimal("400.00"))

    def test_unbalanced_transaction_rejected(self):
        """Attempting to record a transaction where debits != credits raises UnbalancedTransactionError."""
        acc1 = create_account(self.tenant_a, "u_unbal_1")
        acc2 = create_account(self.tenant_a, "u_unbal_2")

        entries = [
            {"account": acc1, "entry_type": EntryType.DEBIT, "amount": Decimal("100.00")},
            {"account": acc2, "entry_type": EntryType.CREDIT, "amount": Decimal("80.00")},
        ]

        with self.assertRaises(UnbalancedTransactionError):
            record_transaction(
                tenant=self.tenant_a,
                transaction_type=TransactionType.ADJUSTMENT,
                entries_data=entries,
            )

    def test_currency_mismatch_rejected(self):
        """Attempting a transaction with mismatched currencies is rejected."""
        acc_inr = create_account(self.tenant_a, "u_inr", currency="INR")
        acc_usd = create_account(self.tenant_a, "u_usd", currency="USD")

        entries = [
            {"account": acc_inr, "entry_type": EntryType.DEBIT, "amount": Decimal("100.00")},
            {"account": acc_usd, "entry_type": EntryType.CREDIT, "amount": Decimal("100.00")},
        ]

        with self.assertRaises(CurrencyMismatchError):
            record_transaction(
                tenant=self.tenant_a,
                transaction_type=TransactionType.TRANSFER,
                entries_data=entries,
            )

    def test_cross_tenant_operation_strictly_rejected(self):
        """Attempting a transaction referencing an account belonging to another tenant is blocked."""
        acc_a = create_account(self.tenant_a, "user_tenant_a")
        acc_b = create_account(self.tenant_b, "user_tenant_b")

        entries = [
            {"account": acc_a, "entry_type": EntryType.DEBIT, "amount": Decimal("50.00")},
            {"account": acc_b, "entry_type": EntryType.CREDIT, "amount": Decimal("50.00")},
        ]

        with self.assertRaises(CrossTenantOperationError):
            record_transaction(
                tenant=self.tenant_a,
                transaction_type=TransactionType.TRANSFER,
                entries_data=entries,
            )

    def test_inactive_account_rejected(self):
        """Operations against SUSPENDED or CLOSED accounts are blocked."""
        acc = create_account(self.tenant_a, "suspended_user")
        acc.status = AccountStatus.SUSPENDED
        acc.save()

        with self.assertRaises(AccountInactiveError):
            deposit(self.tenant_a, "suspended_user", Decimal("100.00"))


class FinanceReconciliationTests(TestCase):
    """Tests for ledger reconciliation and discrepancy detection."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Recon Tenant",
            slug="recon-tenant",
            code="RC",
            country="India",
            timezone="Asia/Kolkata",
            currency="INR",
        )

    def test_reconcile_healthy_account(self):
        """Properly maintained account reconciles with zero difference."""
        deposit(self.tenant, "recon_u1", Decimal("500.00"))
        debit_account(self.tenant, "recon_u1", Decimal("120.00"))

        acc = FinancialAccount.objects.get(tenant=self.tenant, owner_id="recon_u1")
        res = reconcile_account(acc)

        self.assertTrue(res["is_reconciled"])
        self.assertEqual(res["stored_available"], Decimal("380.00"))
        self.assertEqual(res["expected_balance"], Decimal("380.00"))
        self.assertEqual(res["difference"], Decimal("0.00"))

    def test_reconcile_detects_and_repairs_discrepancy(self):
        """Inconsistent materialized balance is detected and repaired when requested."""
        deposit(self.tenant, "recon_u2", Decimal("600.00"))
        acc = FinancialAccount.objects.get(tenant=self.tenant, owner_id="recon_u2")

        # Simulate direct corruption of materialized balance (bypassing services)
        FinancialAccount.objects.filter(id=acc.id).update(available_balance=Decimal("999.00"))
        acc.refresh_from_db()

        # Detection mode
        res = reconcile_account(acc, repair=False)
        self.assertFalse(res["is_reconciled"])
        self.assertEqual(res["stored_available"], Decimal("999.00"))
        self.assertEqual(res["expected_balance"], Decimal("600.00"))
        self.assertEqual(res["difference"], Decimal("399.00"))

        # Repair mode
        res_repair = reconcile_account(acc, repair=True)
        self.assertTrue(res_repair["is_reconciled"])
        acc.refresh_from_db()
        self.assertEqual(acc.available_balance, Decimal("600.00"))

    def test_reconciliation_management_command(self):
        """Management command outputs detected discrepancies and repairs on --repair flag."""
        deposit(self.tenant, "cmd_u1", Decimal("400.00"))
        acc = FinancialAccount.objects.get(tenant=self.tenant, owner_id="cmd_u1")
        FinancialAccount.objects.filter(id=acc.id).update(available_balance=Decimal("800.00"))

        out = StringIO()
        call_command("reconcile_financial_accounts", tenant=self.tenant.slug, stdout=out)
        output_str = out.getvalue()
        self.assertIn("[MISMATCH]", output_str)
        self.assertIn("Inconsistencies Detected: 1", output_str)

        # Re-run with repair
        out_repair = StringIO()
        call_command("reconcile_financial_accounts", tenant=self.tenant.slug, repair=True, stdout=out_repair)
        repair_str = out_repair.getvalue()
        self.assertIn("Repaired account", repair_str)
        self.assertIn("Inconsistencies Repaired: 1", repair_str)


class FinancePostgresConcurrencyTests(TransactionTestCase):
    """
    Real PostgreSQL concurrency tests using true database transactions and threads.
    Validates select_for_update() row locking, negative balance protection, and idempotency races.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Concurrency Tenant",
            slug="concurrency-tenant",
            code="CC",
            country="India",
            timezone="Asia/Kolkata",
            currency="INR",
        )

    def _run_in_thread(self, target, *args, **kwargs):
        res = {"success": False, "val": None, "exc": None}

        def worker():
            try:
                val = target(*args, **kwargs)
                res["success"] = True
                res["val"] = val
            except Exception as e:
                res["exc"] = e
            finally:
                connection.close()

        t = threading.Thread(target=worker)
        return t, res

    def test_concurrent_debit_prevents_overdraft(self):
        """
        Concurrency Test 1:
        Initial balance = ₹1,000.
        Two concurrent threads each attempt to debit ₹700.
        Expected:
        Exactly ONE succeeds, ONE fails with InsufficientFundsError.
        Final available balance = ₹300.00. NEVER negative or lost!
        """
        deposit(self.tenant, "concurrent_buyer", Decimal("1000.00"))

        t1, res1 = self._run_in_thread(
            debit_account,
            tenant=self.tenant,
            user_id="concurrent_buyer",
            amount=Decimal("700.00"),
            description="Debit Thread 1",
        )
        t2, res2 = self._run_in_thread(
            debit_account,
            tenant=self.tenant,
            user_id="concurrent_buyer",
            amount=Decimal("700.00"),
            description="Debit Thread 2",
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in (res1, res2) if r["success"]]
        failures = [r for r in (res1, res2) if not r["success"]]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0]["exc"], InsufficientFundsError)

        user_wallet = FinancialAccount.objects.get(tenant=self.tenant, owner_id="concurrent_buyer")
        self.assertEqual(user_wallet.available_balance, Decimal("300.00"))

        # Verify ledger reconciles
        recon = reconcile_account(user_wallet)
        self.assertTrue(recon["is_reconciled"])
        self.assertEqual(recon["expected_balance"], Decimal("300.00"))

    def test_concurrent_credit_and_debit_operations(self):
        """
        Concurrency Test 2:
        Multiple concurrent credits and debits executed across 4 threads.
        Initial balance: ₹500.00.
        Operations:
        - Thread 1: Credit ₹500.00
        - Thread 2: Debit ₹300.00
        - Thread 3: Credit ₹200.00
        - Thread 4: Debit ₹100.00
        Expected final balance: 500 + 500 - 300 + 200 - 100 = ₹800.00.
        Materialized balance == Ledger-derived balance.
        """
        deposit(self.tenant, "multi_op_user", Decimal("500.00"))

        t1, res1 = self._run_in_thread(deposit, self.tenant, "multi_op_user", Decimal("500.00"))
        t2, res2 = self._run_in_thread(debit_account, self.tenant, "multi_op_user", Decimal("300.00"))
        t3, res3 = self._run_in_thread(deposit, self.tenant, "multi_op_user", Decimal("200.00"))
        t4, res4 = self._run_in_thread(debit_account, self.tenant, "multi_op_user", Decimal("100.00"))

        for t in (t1, t2, t3, t4):
            t.start()
        for t in (t1, t2, t3, t4):
            t.join()

        # All operations must have succeeded
        for idx, res in enumerate((res1, res2, res3, res4)):
            self.assertTrue(res["success"], f"Thread {idx+1} failed with: {res['exc']}")

        user_wallet = FinancialAccount.objects.get(tenant=self.tenant, owner_id="multi_op_user")
        self.assertEqual(user_wallet.available_balance, Decimal("800.00"))

        recon = reconcile_account(user_wallet)
        self.assertTrue(recon["is_reconciled"])
        self.assertEqual(recon["expected_balance"], Decimal("800.00"))

    def test_concurrent_duplicate_idempotency_requests(self):
        """
        Concurrency Test 3:
        Two threads submit the exact same operation with the same idempotency key simultaneously.
        Expected:
        Both threads succeed returning the exact same LedgerTransaction.
        Only ONE transaction is created in the ledger, and balance is credited only once.
        """
        key = "concurrent-idemp-key-999"

        t1, res1 = self._run_in_thread(
            deposit,
            tenant=self.tenant,
            user_id="idemp_race_user",
            amount=Decimal("450.00"),
            idempotency_key=key,
        )
        t2, res2 = self._run_in_thread(
            deposit,
            tenant=self.tenant,
            user_id="idemp_race_user",
            amount=Decimal("450.00"),
            idempotency_key=key,
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertTrue(res1["success"], f"Thread 1 failed: {res1['exc']}")
        self.assertTrue(res2["success"], f"Thread 2 failed: {res2['exc']}")

        self.assertEqual(res1["val"].id, res2["val"].id)
        self.assertEqual(LedgerTransaction.objects.filter(tenant=self.tenant, idempotency_key=key).count(), 1)

        user_wallet = FinancialAccount.objects.get(tenant=self.tenant, owner_id="idemp_race_user")
        self.assertEqual(user_wallet.available_balance, Decimal("450.00"))
