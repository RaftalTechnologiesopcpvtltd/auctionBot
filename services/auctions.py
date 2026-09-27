"""Auction domain services.

Encapsulates headless auction lifecycle transitions, validation, and outcome calculations.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
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
def close_auction(auction: Auction) -> Auction:
    """Closes an auction and calculates the final outcome (SOLD vs UNSOLD).

    If highest bid exists:
      - Sets status = SOLD
      - Records winner_id and winning_price
      - Updates listing remaining_quantity = 0
    If no bids exist:
      - Sets status = UNSOLD
    """
    if auction.status in (AuctionStatus.SOLD, AuctionStatus.UNSOLD, AuctionStatus.CANCELLED):
        raise ValidationError(f"Auction is already completed with status '{auction.status}'.")

    # Lock auction row for outcome determination
    auction = Auction.objects.select_for_update().get(id=auction.id)

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
