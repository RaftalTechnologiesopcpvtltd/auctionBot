"""Concurrent Bidding Domain and Application Services.

Implements PostgreSQL row-level locking (SELECT FOR UPDATE) within atomic database transactions,
guaranteeing state consistency under high-concurrency bidding competition.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from typing import Any, Optional, Tuple, Union
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.tenants.models import Tenant
from apps.bidding.models import Auction, AuctionStatus, Bid
from apps.listings.models import ListingStatus

logger = logging.getLogger(__name__)


class BiddingError(ValidationError):
    """Base exception for all bidding domain errors."""
    pass


class AuctionNotActiveError(BiddingError):
    """Raised when bidding is attempted on an auction that is not ACTIVE."""
    pass


class AuctionClosedError(BiddingError):
    """Raised when bidding is attempted on a closed or expired auction."""
    pass


class BidTooLowError(BiddingError):
    """Raised when bid amount is below the minimum required increment."""
    pass


class BidderNotEligibleError(BiddingError):
    """Raised when bidder is not eligible (e.g. seller shill bidding)."""
    pass


class DuplicateBidError(BiddingError):
    """Raised when an identical idempotency key is submitted with conflicting parameters."""
    pass


class AuctionAlreadySoldError(BiddingError):
    """Raised when attempting to place a bid on an already sold auction."""
    pass


@transaction.atomic
def place_bid(
    auction_or_id: Union[Auction, int],
    bidder_id: str,
    amount: Union[Decimal, str, int, float],
    bidder_name: str = "",
    idempotency_key: Optional[str] = None,
    as_of: Optional[datetime] = None,
    tenant: Optional[Tenant] = None,
) -> Tuple[Bid, Auction]:
    """Places a new bid on an active auction with atomic PostgreSQL row locking.

    Concurrency and Invariant Rules:
    1. Locks the Auction row using SELECT FOR UPDATE to prevent race conditions.
    2. Enforces idempotent submission if idempotency_key is provided.
    3. Verifies auction status is strictly ACTIVE.
    4. Validates that current timestamp falls within [start_at, end_at].
    5. Prohibits seller self-bidding (anti-shill rule).
    6. Verifies amount >= minimum required next bid (starting_price or current_price + increment).
    7. Evaluates instant buy-now threshold.
    8. Applies anti-sniping extension if bid arrives within the configured threshold window.
    9. Atomically persists Bid and updates Auction leading state.
    """
    bidder_id = str(bidder_id).strip()
    amount = Decimal(str(amount))

    # Resolve auction id and tenant
    if isinstance(auction_or_id, Auction):
        auction_id = auction_or_id.id
        resolved_tenant = tenant or auction_or_id.tenant
    else:
        auction_id = int(auction_or_id)
        resolved_tenant = tenant

    # 1. Acquire pessimistic row lock on Auction
    query = Auction.objects.select_for_update().select_related("listing", "tenant")
    if resolved_tenant:
        query = query.filter(tenant=resolved_tenant)

    try:
        auction = query.get(id=auction_id)
    except Auction.DoesNotExist:
        raise ValidationError(f"Auction #{auction_id} does not exist for the specified tenant.")

    # 2. Check Idempotency Key
    if idempotency_key:
        idempotency_key = idempotency_key.strip()
        existing_bid = Bid.objects.filter(
            tenant=auction.tenant,
            auction=auction,
            idempotency_key=idempotency_key,
        ).first()
        if existing_bid:
            logger.info(
                "Idempotent bid request recognized for key '%s' on Auction #%s; returning existing Bid #%s.",
                idempotency_key,
                auction.id,
                existing_bid.id,
            )
            return existing_bid, auction

    # 3. Check Auction Status
    if auction.status in (AuctionStatus.SOLD, AuctionStatus.CLOSED, AuctionStatus.UNSOLD, AuctionStatus.CANCELLED):
        raise AuctionClosedError(f"Cannot bid on auction #{auction.id}: status is already '{auction.status}'.")

    if auction.status != AuctionStatus.ACTIVE:
        raise AuctionNotActiveError(f"Cannot bid on auction #{auction.id}: status must be ACTIVE (currently '{auction.status}').")

    # 4. Check Temporal Window
    now = as_of or timezone.now()
    if now < auction.start_at:
        raise AuctionNotActiveError(f"Auction #{auction.id} bidding has not opened yet (opens at {auction.start_at}).")

    if now > auction.end_at:
        raise AuctionClosedError(f"Auction #{auction.id} bidding window has expired (closed at {auction.end_at}).")

    # 5. Anti-Shill Rule: Seller cannot bid on own listing
    if str(auction.listing.seller_id) == bidder_id:
        raise BidderNotEligibleError("Sellers are strictly prohibited from bidding on their own listings.")

    # 6. Minimum Increment Validation
    min_required = auction.min_next_bid
    if amount < min_required:
        raise BidTooLowError(
            f"Bid amount ${amount} is below the minimum required bid of ${min_required}."
        )

    # 7. Create Bid Record
    bid = Bid(
        tenant=auction.tenant,
        auction=auction,
        bidder_id=bidder_id,
        bidder_name=bidder_name,
        amount=amount,
        idempotency_key=idempotency_key,
    )
    bid.full_clean()
    bid.save()

    # 8. Check Instant Buy-Now Buyout
    if auction.buy_now_price and amount >= auction.buy_now_price:
        auction.status = AuctionStatus.SOLD
        auction.winner_id = bidder_id
        auction.winning_price = amount
        auction.current_price = amount
        auction.highest_bid = bid

        listing = auction.listing
        listing.remaining_quantity = 0
        listing.status = ListingStatus.CLOSED
        listing.save(update_fields=["remaining_quantity", "status", "updated_at"])

        logger.info(
            "Auction #%s immediately SOLD via Buy-Now at $%s to bidder '%s'.",
            auction.id,
            amount,
            bidder_id,
        )
    else:
        # Regular bid: update current price and leading bid
        auction.current_price = amount
        auction.highest_bid = bid

        # 9. Anti-Sniping Dynamic Extension
        if auction.anti_sniping_seconds > 0:
            time_remaining = (auction.end_at - now).total_seconds()
            if 0 <= time_remaining <= auction.anti_sniping_seconds:
                auction.end_at = auction.end_at + timedelta(seconds=auction.extension_seconds)
                auction.extension_count += 1
                logger.info(
                    "Anti-sniping triggered for Auction #%s by bid from '%s': extended by %ss to %s (count: %s).",
                    auction.id,
                    bidder_id,
                    auction.extension_seconds,
                    auction.end_at,
                    auction.extension_count,
                )

    auction.save(
        update_fields=[
            "current_price",
            "highest_bid",
            "status",
            "winner_id",
            "winning_price",
            "end_at",
            "extension_count",
            "updated_at",
        ]
    )

    logger.info(
        "Successfully placed Bid #%s ($%s) on Auction #%s by bidder '%s'.",
        bid.id,
        amount,
        auction.id,
        bidder_id,
    )
    return bid, auction
