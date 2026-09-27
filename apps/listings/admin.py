"""Django admin registration for Listings."""
from django.contrib import admin
from .models import Listing


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "tenant",
        "seller_id",
        "listing_type",
        "status",
        "quantity",
        "remaining_quantity",
        "created_at",
    )
    list_filter = (
        "tenant",
        "listing_type",
        "status",
        "category",
    )
    search_fields = (
        "title",
        "seller_id",
        "seller_username",
        "description",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
