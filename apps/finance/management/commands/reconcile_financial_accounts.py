from decimal import Decimal
from django.core.management.base import BaseCommand
from apps.finance.models import FinancialAccount
from apps.finance.services import reconcile_account


class Command(BaseCommand):
    help = "Reconciles materialized financial account balances against immutable ledger entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            help="Filter reconciliation by tenant slug",
            default=None,
        )
        parser.add_argument(
            "--account-id",
            type=int,
            help="Reconcile a specific financial account ID",
            default=None,
        )
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Explicitly repair detected inconsistencies to match ledger totals.",
            default=False,
        )

    def handle(self, *args, **options):
        tenant_slug = options.get("tenant")
        account_id = options.get("account_id")
        repair = options.get("repair", False)

        qs = FinancialAccount.objects.select_related("tenant").all()
        if tenant_slug:
            qs = qs.filter(tenant__slug=tenant_slug)
        if account_id:
            qs = qs.filter(id=account_id)

        total_checked = 0
        inconsistencies = 0
        repaired = 0

        self.stdout.write("=" * 70)
        self.stdout.write(f"RECONCILIATION AUDIT (Repair Mode: {'ENABLED' if repair else 'REPORT ONLY'})")
        self.stdout.write("=" * 70)

        for account in qs:
            total_checked += 1
            res = reconcile_account(account, repair=repair)

            if not res["is_reconciled"] or res.get("was_repaired"):
                inconsistencies += 1
                diff = res["difference"]
                self.stdout.write(
                    self.style.WARNING(
                        f"[MISMATCH] Tenant: {res['tenant']} | Account #{res['account_id']} ({res['owner_id']}) "
                        f"Type: {res['account_type']} | Currency: {res['currency']} | "
                        f"Stored: {res['stored_available']} | Expected: {res['expected_balance']} | "
                        f"Difference: {diff}"
                    )
                )
                if res.get("was_repaired"):
                    repaired += 1
                    self.stdout.write(self.style.SUCCESS(f"  --> Repaired account #{res['account_id']}"))
            else:
                self.stdout.write(
                    f"[OK] Tenant: {res['tenant']} | Account #{res['account_id']} ({res['owner_id']}) | "
                    f"Balance: {res['stored_available']} {res['currency']} ({res['entry_count']} entries)"
                )

        self.stdout.write("=" * 70)
        summary = (
            f"Total Accounts Checked: {total_checked} | "
            f"Inconsistencies Detected: {inconsistencies}"
        )
        if repair:
            summary += f" | Inconsistencies Repaired: {repaired}"
        self.stdout.write(self.style.SUCCESS(summary) if inconsistencies == 0 else self.style.WARNING(summary))
