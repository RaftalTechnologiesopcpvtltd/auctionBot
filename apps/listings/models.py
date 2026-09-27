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


class SellerStatus(models.TextChoices):
    PENDING = "PENDING", "Pending Approval"
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    REJECTED = "REJECTED", "Rejected"


class Seller(TenantOwnedModel):
    """Represents a verified or registered seller within a tenant."""

    telegram_user = models.ForeignKey(
        "telegram_engine.TelegramUser",
        on_delete=models.CASCADE,
        related_name="seller_profiles",
        help_text="Telegram account linked to this seller profile.",
    )
    seller_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="External/headless identifier for the seller (e.g. str(telegram_user_id)).",
    )
    business_name = models.CharField(
        max_length=150,
        help_text="Trading name, farm name, or individual seller brand.",
    )
    contact_name = models.CharField(
        max_length=150,
        help_text="Full legal or representative name of contact person.",
    )
    phone = models.CharField(
        max_length=50,
        help_text="Contact telephone / WhatsApp number.",
    )
    email = models.EmailField(
        blank=True,
        default="",
        help_text="Contact email address.",
    )
    address = models.TextField(
        blank=True,
        default="",
        help_text="Physical farm or dispatch address.",
    )
    status = models.CharField(
        max_length=20,
        choices=SellerStatus.choices,
        default=SellerStatus.ACTIVE,
        db_index=True,
        help_text="Seller onboarding and moderation status.",
    )

    class Meta:
        verbose_name = "Seller"
        verbose_name_plural = "Sellers"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "telegram_user"],
                name="unique_tenant_seller_telegram_user",
            ),
            models.UniqueConstraint(
                fields=["tenant", "seller_id"],
                name="unique_tenant_seller_id",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"[{self.tenant.code}] {self.business_name} ({self.contact_name})"


class ListingImage(TenantOwnedModel):
    """Media assets attached to a commercial listing."""

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="images",
        help_text="Parent listing.",
    )
    image = models.ImageField(
        upload_to="listings/%Y/%m/",
        blank=True,
        null=True,
        help_text="Local/cloud stored image asset.",
    )
    file_url = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Direct URL to media file if stored externally or relative path.",
    )
    telegram_file_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Telegram file_id for quick re-serving or downloading.",
    )
    caption = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order in gallery.",
    )

    class Meta:
        verbose_name = "Listing Image"
        verbose_name_plural = "Listing Images"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["tenant", "listing"]),
        ]

    def __str__(self) -> str:
        return f"Image #{self.id} for Listing #{self.listing_id}"

