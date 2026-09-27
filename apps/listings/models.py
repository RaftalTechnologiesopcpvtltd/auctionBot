"""Listing domain models for AuctionBot.

Defines:
- Listing: Multi-tenant catalog item supporting Auction and Buy-Now sale types.
"""
from django.core.exceptions import ValidationError
from django.db import models
from apps.tenants.models import TenantOwnedModel


class ListingType(models.TextChoices):
    """Supported commercial formats for a listing."""
    AUCTION = "AUCTION", "Auction"
    BUY_NOW = "BUY_NOW", "Buy It Now"


class ListingStatus(models.TextChoices):
    """Lifecycle statuses for a listing."""
    DRAFT = "DRAFT", "Draft"
    PENDING = "PENDING", "Pending Approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    CLOSED = "CLOSED", "Closed"


class Listing(TenantOwnedModel):
    """A tenant-owned commercial listing (Auction or Fixed Price item)."""

    seller_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="External/headless identifier for the seller.",
    )
    seller_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Optional display handle of the seller.",
    )
    title = models.CharField(
        max_length=200,
        help_text="Headline or title of the listing.",
    )
    description = models.TextField(
        blank=True,
        help_text="Comprehensive item description and terms.",
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        default="General",
        db_index=True,
        help_text="Taxonomy category for the listing.",
    )
    listing_type = models.CharField(
        max_length=20,
        choices=ListingType.choices,
        default=ListingType.AUCTION,
        db_index=True,
        help_text="Format: Auction or Buy It Now.",
    )
    status = models.CharField(
        max_length=20,
        choices=ListingStatus.choices,
        default=ListingStatus.PENDING,
        db_index=True,
        help_text="Current moderation/lifecycle status.",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text="Total units available at creation.",
    )
    remaining_quantity = models.PositiveIntegerField(
        default=1,
        help_text="Units currently remaining unsold.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extensible key-value metadata (e.g. breed, size, shipping, location).",
    )

    class Meta:
        verbose_name = "Listing"
        verbose_name_plural = "Listings"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "listing_type"],
                name="listing_tenant_status_type_idx",
            ),
            models.Index(
                fields=["tenant", "seller_id"],
                name="listing_tenant_seller_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.remaining_quantity > self.quantity:
            raise ValidationError(
                {"remaining_quantity": "Remaining quantity cannot exceed total quantity."}
            )

    def __str__(self):
        return f"[{self.tenant.code}] {self.title} (#{self.id})"
