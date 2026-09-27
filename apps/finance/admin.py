from django.contrib import admin
from apps.finance.models import FinancialAccount, LedgerTransaction, LedgerEntry


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False
    readonly_fields = ("account", "entry_type", "amount", "currency", "created_at", "metadata")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FinancialAccount)
class FinancialAccountAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "owner_id",
        "account_type",
        "currency",
        "available_balance",
        "held_balance",
        "status",
        "created_at",
    )
    list_filter = ("tenant", "account_type", "currency", "status")
    search_fields = ("owner_id", "tenant__slug", "tenant__name")
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion if account has ledger history
        if obj and obj.ledger_entries.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "transaction_type",
        "status",
        "reference_type",
        "reference_id",
        "idempotency_key",
        "created_at",
    )
    list_filter = ("tenant", "transaction_type", "status", "created_at")
    search_fields = ("idempotency_key", "reference_id", "description", "tenant__slug")
    readonly_fields = (
        "tenant",
        "transaction_type",
        "status",
        "reference_type",
        "reference_id",
        "idempotency_key",
        "description",
        "created_at",
        "completed_at",
        "metadata",
    )
    inlines = [LedgerEntryInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "transaction",
        "account",
        "entry_type",
        "amount",
        "currency",
        "created_at",
    )
    list_filter = ("entry_type", "currency", "created_at")
    search_fields = ("account__owner_id", "transaction__idempotency_key")
    readonly_fields = (
        "transaction",
        "account",
        "entry_type",
        "amount",
        "currency",
        "created_at",
        "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
