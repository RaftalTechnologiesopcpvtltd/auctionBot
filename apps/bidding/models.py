"""Auction and Bidding domain models for AuctionBot.

Defines:
- Auction: Manages the time window, pricing rules, and lifecycle of an auction.
- Bid: Represents an individual monetary bid on an active auction.
"""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.tenants.models import TenantOwnedModel


class AuctionStatus(models.TextChoices):
    """Lifecycle statuses for an auction."""
    SCHEDULED = "SCHEDULED", "Scheduled"
    ACTIVE = "ACTIVE", "Active / Open"
    CLOSED = "CLOSED", "Closed"
    SOLD = "SOLD", "Sold"
    UNSOLD = "UNSOLD", "Unsold"
    CANCELLED = "CANCELLED", "Cancelled"


class Auction(TenantOwnedModel):
    """Time-bound auction lifecycle attached to a Catalog Listing."""

    listing = models.OneToOneField(
        "listings.Listing",
        on_delete=models.PROTECT,
        related_name="auction",
        help_text="The underlying catalog item being auctioned.",
    )
    starting_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Minimum opening bid price.",
    )
    bid_increment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("5.00"),
        help_text="Minimum monetary step required between bids.",
    )
    current_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Highest current valid bid amount, or 0.00 if no bids placed.",
    )
    buy_now_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional price to instantly purchase and close the auction.",
    )
    start_at = models.DateTimeField(
        db_index=True,
        help_text="Scheduled UTC datetime when bidding begins.",
    )
    end_at = models.DateTimeField(
        db_index=True,
        help_text="Scheduled UTC datetime when bidding ends.",
    )
    status = models.CharField(
        max_length=20,
        choices=AuctionStatus.choices,
        default=AuctionStatus.SCHEDULED,
        db_index=True,
        help_text="Current operational state of the auction.",
    )
    highest_bid = models.ForeignKey(
        "bidding.Bid",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Reference to the current leading bid.",
    )
    winner_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text="Headless identifier of the winning bidder upon close.",
    )
    winning_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Final winning hammer price.",
    )
    is_reminder_sent = models.BooleanField(
        default=False,
        help_text="Flag indicating whether the 15-minute close reminder was dispatched.",
    )

    class Meta:
        verbose_name = "Auction"
        verbose_name_plural = "Auctions"
        ordering = ["-start_at"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "end_at"],
                name="auction_tenant_status_end_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "Auction end time must be strictly after start time."})

        if self.starting_price is not None and self.starting_price <= Decimal("0.00"):
            raise ValidationError({"starting_price": "Starting price must be strictly positive."})

        if self.bid_increment is not None and self.bid_increment <= Decimal("0.00"):
            raise ValidationError({"bid_increment": "Bid increment must be strictly positive."})

        if self.buy_now_price is not None and self.starting_price is not None:
            if self.buy_now_price <= self.starting_price:
                raise ValidationError({"buy_now_price": "Buy-Now price must exceed starting price."})

        # Enforce that auction tenant matches the listing tenant
        if hasattr(self, "listing") and self.listing and self.listing.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "Auction tenant must match the associated Listing tenant."})

    @property
    def min_next_bid(self) -> Decimal:
        """Calculates the minimum acceptable next bid amount."""
        if self.highest_bid_id is None or self.current_price == Decimal("0.00"):
            return self.starting_price
        return self.current_price + self.bid_increment

    def is_bidding_open(self, as_of=None) -> bool:
        """Determines if the auction is actively accepting bids at the given time."""
        now = as_of or timezone.now()
        return self.status == AuctionStatus.ACTIVE and self.start_at <= now <= self.end_at

    def __str__(self):
        return f"Auction #{self.id} for Listing #{self.listing_id} ({self.status})"


class Bid(TenantOwnedModel):
    """An individual monetary bid submitted on an Auction."""

    auction = models.ForeignKey(
        Auction,
        on_delete=models.PROTECT,
        related_name="bids",
        help_text="The auction receiving this bid.",
    )
    bidder_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Headless identifier of the bidding participant.",
    )
    bidder_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Display handle or name of the bidder.",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Monetary amount of this bid.",
    )
    placed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when the bid was registered.",
    )

    class Meta:
        verbose_name = "Bid"
        verbose_name_plural = "Bids"
        ordering = ["-amount", "-placed_at"]
        indexes = [
            models.Index(
                fields=["auction", "-amount", "-placed_at"],
                name="bid_auction_rank_idx",
            ),
            models.Index(
                fields=["tenant", "bidder_id"],
                name="bid_tenant_bidder_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Bid amount must be strictly positive."})

        if hasattr(self, "auction") and self.auction and self.auction.tenant_id != self.tenant_id:
            raise ValidationError({"tenant": "Bid tenant must match the associated Auction tenant."})

    def __str__(self):
        return f"${self.amount} on Auction #{self.auction_id} by {self.bidder_name or self.bidder_id}"
