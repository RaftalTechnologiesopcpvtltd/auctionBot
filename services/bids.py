"""Bidding domain services.

Encapsulates headless bid placement, step increment validation, seller self-bid prevention,
and auction state synchronization.
"""
from decimal import Decimal
from typing import Tuple, Optional
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.bidding.models import Auction, AuctionStatus, Bid


@transaction.atomic
def place_bid(
    auction: Auction,
    bidder_id: str,
    amount: Decimal,
    bidder_name: str = "",
    as_of: Optional[timezone.datetime] = None,
) -> Tuple[Bid, Auction]:
    """Places a new bid on an active auction with full domain rule enforcement.

    Enforces:
    1. Auction must be in ACTIVE status.
    2. Current timestamp must be within start_at and end_at.
    3. Seller cannot bid on their own listing (anti-shill bidding rule).
    4. Bid amount must be >= minimum required next bid.
    5. Automatic buy-now buyout if bid matches or exceeds buy_now_price.
    """
    bidder_id = str(bidder_id).strip()
    amount = Decimal(str(amount))

    # Lock auction row to prevent race conditions during calculation
    auction = Auction.objects.select_for_update().select_related("listing", "tenant").get(id=auction.id)
    now = as_of or timezone.now()

    # Rule 1 & 2: Active & time window validation
    if auction.status != AuctionStatus.ACTIVE:
        raise ValidationError(f"Cannot bid on auction with status '{auction.status}'. Must be ACTIVE.")

    if not (auction.start_at <= now <= auction.end_at):
        raise ValidationError("Bidding is closed: current time is outside the auction start/end window.")

    # Rule 3: Anti-shill bidding (seller cannot bid on own listing)
    if auction.listing.seller_id == bidder_id:
        raise ValidationError("Sellers are strictly prohibited from bidding on their own listings.")

    # Rule 4: Minimum increment calculation
    min_required = auction.min_next_bid
    if amount < min_required:
        raise ValidationError(
            f"Bid amount ${amount} is below the minimum required bid of ${min_required}."
        )

    # Create the Bid record scoped to the auction's tenant
    bid = Bid(
        tenant=auction.tenant,
        auction=auction,
        bidder_id=bidder_id,
        bidder_name=bidder_name,
        amount=amount,
    )
    bid.full_clean()
    bid.save()

    # Update auction leading price and highest bid
    auction.current_price = amount
    auction.highest_bid = bid

    # Check for immediate buy-now buyout
    if auction.buy_now_price and amount >= auction.buy_now_price:
        auction.status = AuctionStatus.SOLD
        auction.winner_id = bidder_id
        auction.winning_price = amount
        auction.listing.remaining_quantity = 0
        auction.listing.save(update_fields=["remaining_quantity", "updated_at"])

    auction.save(update_fields=["current_price", "highest_bid", "status", "winner_id", "winning_price", "updated_at"])
    return bid, auction
