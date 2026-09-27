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


from .models import Seller, ListingImage


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "business_name",
        "contact_name",
        "tenant",
        "phone",
        "email",
        "status",
        "created_at",
    )
    list_filter = ("tenant", "status")
    search_fields = ("business_name", "contact_name", "phone", "email", "seller_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ListingImage)
class ListingImageAdmin(admin.ModelAdmin):
    list_display = ("id", "listing", "tenant", "order", "created_at")
    list_filter = ("tenant",)
    readonly_fields = ("created_at", "updated_at")

