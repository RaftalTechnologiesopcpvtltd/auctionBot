"""Listing domain services.

Encapsulates headless lifecycle management for listings without coupling to HTTP or Telegram.
"""
from typing import Optional
from django.core.exceptions import ValidationError
from apps.listings.models import Listing, ListingStatus, ListingType
from apps.tenants.models import Tenant


def create_listing(
    tenant: Tenant,
    seller_id: str,
    title: str,
    seller_username: str = "",
    description: str = "",
    category: str = "General",
    listing_type: str = ListingType.AUCTION,
    quantity: int = 1,
    metadata: Optional[dict] = None,
) -> Listing:
    """Creates a new catalog listing scoped to a tenant."""
    listing = Listing(
        tenant=tenant,
        seller_id=str(seller_id),
        seller_username=seller_username,
        title=title,
        description=description,
        category=category,
        listing_type=listing_type,
        status=ListingStatus.PENDING,
        quantity=quantity,
        remaining_quantity=quantity,
        metadata=metadata or {},
    )
    listing.full_clean()
    listing.save()
    return listing


def approve_listing(listing: Listing) -> Listing:
    """Approves a pending listing for operational scheduling."""
    if listing.status != ListingStatus.PENDING:
        raise ValidationError(f"Cannot approve listing with status '{listing.status}'. Must be PENDING.")
    listing.status = ListingStatus.APPROVED
    listing.save(update_fields=["status", "updated_at"])
    return listing


def reject_listing(listing: Listing, reason: Optional[str] = None) -> Listing:
    """Rejects a pending listing."""
    if listing.status != ListingStatus.PENDING:
        raise ValidationError(f"Cannot reject listing with status '{listing.status}'. Must be PENDING.")
    listing.status = ListingStatus.REJECTED
    if reason and isinstance(listing.metadata, dict):
        listing.metadata["rejection_reason"] = reason
        listing.save(update_fields=["status", "metadata", "updated_at"])
    else:
        listing.save(update_fields=["status", "updated_at"])
    return listing


def close_listing(listing: Listing) -> Listing:
    """Marks a listing as closed."""
    listing.status = ListingStatus.CLOSED
    listing.save(update_fields=["status", "updated_at"])
    return listing
