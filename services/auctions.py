"""Auction domain services.

Encapsulates headless auction lifecycle transitions, validation, and outcome calculations.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union, Any
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.bidding.models import Auction, AuctionStatus
from apps.listings.models import Listing, ListingStatus, ListingType


def create_auction(
    listing: Listing,
    starting_price: Decimal,
    start_at: datetime,
    end_at: datetime,
    bid_increment: Decimal = Decimal("5.00"),
    buy_now_price: Optional[Decimal] = None,
) -> Auction:
    """Creates a new scheduled auction attached to an approved listing."""
    if listing.listing_type != ListingType.AUCTION:
        raise ValidationError("Cannot create an auction for a non-AUCTION listing.")

    if listing.status not in (ListingStatus.APPROVED, ListingStatus.PENDING):
        raise ValidationError(f"Listing must be APPROVED or PENDING to create an auction (current: {listing.status}).")

    auction = Auction(
        tenant=listing.tenant,
        listing=listing,
        starting_price=Decimal(str(starting_price)),
        bid_increment=Decimal(str(bid_increment)),
        current_price=Decimal("0.00"),
        buy_now_price=Decimal(str(buy_now_price)) if buy_now_price is not None else None,
        start_at=start_at,
        end_at=end_at,
        status=AuctionStatus.SCHEDULED,
    )
    auction.full_clean()
    auction.save()
    return auction


def start_auction(auction: Auction, as_of: Optional[datetime] = None) -> Auction:
    """Activates an auction if current time has reached start_at."""
    if auction.status != AuctionStatus.SCHEDULED:
        raise ValidationError(f"Cannot start auction with status '{auction.status}'. Must be SCHEDULED.")

    now = as_of or timezone.now()
    if now < auction.start_at:
        raise ValidationError(f"Cannot start auction before scheduled start time ({auction.start_at}).")

    auction.status = AuctionStatus.ACTIVE
    auction.save(update_fields=["status", "updated_at"])
    return auction


@transaction.atomic
def close_auction(
    auction_or_id: Union[Auction, int],
    as_of: Optional[datetime] = None,
    tenant: Optional[Any] = None,
) -> Auction:
    """Closes an auction and calculates the final outcome (SOLD vs UNSOLD) with row-level locking.

    Concurrency and Idempotency Rules:
    1. Locks the Auction row with SELECT FOR UPDATE to serialize closing operations.
    2. If the auction is already SOLD, UNSOLD, CLOSED, or CANCELLED, returns immediately (idempotent).
    3. Verifies that current time has reached or passed end_at (respecting anti-sniping extensions).
    4. If bids exist: designates highest bidder as winner, records winning_price, updates listing.
    5. If no bids exist: marks status as UNSOLD.
    """
    if isinstance(auction_or_id, Auction):
        auction_id = auction_or_id.id
    else:
        auction_id = int(auction_or_id)

    query = Auction.objects.select_for_update().select_related("listing", "tenant")
    if tenant:
        query = query.filter(tenant=tenant)

    auction = query.get(id=auction_id)

    # Idempotent guard: if already closed, return current state without re-processing
    if auction.status in (AuctionStatus.SOLD, AuctionStatus.UNSOLD, AuctionStatus.CLOSED, AuctionStatus.CANCELLED):
        return auction

    now = as_of or timezone.now()
    if now < auction.end_at:
        raise ValidationError(
            f"Cannot close auction #{auction.id}: end time ({auction.end_at}) has not passed yet."
        )

    # Check for leading bid
    leading_bid = auction.bids.order_by("-amount", "-placed_at").first()

    if leading_bid:
        auction.status = AuctionStatus.SOLD
        auction.winner_id = leading_bid.bidder_id
        auction.winning_price = leading_bid.amount
        auction.highest_bid = leading_bid
        # Deplete listing stock
        listing = auction.listing
        listing.remaining_quantity = 0
        listing.status = ListingStatus.CLOSED
        listing.save(update_fields=["remaining_quantity", "status", "updated_at"])
    else:
        auction.status = AuctionStatus.UNSOLD

    auction.save(update_fields=["status", "winner_id", "winning_price", "highest_bid", "updated_at"])
    return auction


def cancel_auction(auction: Auction, reason: Optional[str] = None) -> Auction:
    """Cancels an active or scheduled auction."""
    if auction.status in (AuctionStatus.SOLD, AuctionStatus.CANCELLED):
        raise ValidationError(f"Cannot cancel auction with status '{auction.status}'.")

    auction.status = AuctionStatus.CANCELLED
    auction.save(update_fields=["status", "updated_at"])
    return auction
