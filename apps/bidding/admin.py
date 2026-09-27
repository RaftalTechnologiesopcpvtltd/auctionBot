"""Django admin registration for Auctions and Bids."""
from django.contrib import admin
from .models import Auction, Bid


@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "listing",
        "tenant",
        "starting_price",
        "current_price",
        "status",
        "start_at",
        "end_at",
        "winner_id",
        "winning_price",
    )
    list_filter = (
        "tenant",
        "status",
    )
    search_fields = (
        "listing__title",
        "winner_id",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "auction",
        "tenant",
        "bidder_id",
        "bidder_name",
        "amount",
        "placed_at",
    )
    list_filter = (
        "tenant",
    )
    search_fields = (
        "bidder_id",
        "bidder_name",
        "auction__listing__title",
    )
    readonly_fields = (
        "placed_at",
        "created_at",
        "updated_at",
    )
