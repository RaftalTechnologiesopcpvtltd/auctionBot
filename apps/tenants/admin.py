"""Django admin registration for the Tenants app."""
from django.contrib import admin
from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin interface for managing Tenant organizations."""

    list_display = (
        "name",
        "code",
        "slug",
        "country",
        "currency",
        "timezone",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        "country",
        "currency",
    )
    search_fields = (
        "name",
        "code",
        "slug",
        "country",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Tenant Identification",
            {
                "fields": (
                    "name",
                    "code",
                    "slug",
                    "country",
                )
            },
        ),
        (
            "Localization & Economics",
            {
                "fields": (
                    "currency",
                    "timezone",
                )
            },
        ),
        (
            "Status & Lifecycle",
            {
                "fields": (
                    "is_active",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
