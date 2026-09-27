import logging
from typing import Optional
from django.core.exceptions import ValidationError
from apps.listings.models import Listing, ListingImage, ListingStatus, ListingType
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


def _notify_seller(listing: Listing, message_text: str) -> None:
    """Best-effort seller notification via Telegram."""
    try:
        from apps.listings.models import Seller
        from apps.telegram_engine.models import BotType, TelegramBotConfig
        from services.telegram import TelegramService

        seller = Seller.objects.filter(
            tenant=listing.tenant,
            seller_id=listing.seller_id
        ).select_related("telegram_user").first()

        if not seller or not seller.telegram_user:
            return

        bot_config = (
            TelegramBotConfig.objects.filter(tenant=listing.tenant, bot_type=BotType.SELLER, is_active=True).first()
            or TelegramBotConfig.objects.filter(tenant=listing.tenant, bot_type=BotType.UNIFIED, is_active=True).first()
        )
        if not bot_config:
            return

        svc = TelegramService(bot_config)
        svc.send_message(chat_id=seller.telegram_user.chat_id, text=message_text)
    except Exception as exc:
        logger.warning("Could not send Telegram notification to seller of listing %s: %s", listing.id, exc)


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
    status: str = ListingStatus.PENDING,
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
        status=status,
        quantity=quantity,
        remaining_quantity=quantity,
        metadata=metadata or {},
    )
    listing.full_clean()
    listing.save()
    return listing


def attach_listing_image(
    listing: Listing,
    image=None,
    file_url: str = "",
    telegram_file_id: str = "",
    caption: str = "",
    order: int = 0,
) -> ListingImage:
    """Attaches an image asset to a listing."""
    img = ListingImage(
        tenant=listing.tenant,
        listing=listing,
        image=image,
        file_url=file_url,
        telegram_file_id=telegram_file_id,
        caption=caption,
        order=order,
    )
    img.save()
    return img


def ensure_active_auction_for_listing(listing: Listing):
    """Ensures an active Auction record exists for an approved AUCTION listing."""
    from decimal import Decimal
    from django.utils import timezone
    from apps.bidding.models import Auction, AuctionStatus

    if listing.listing_type != ListingType.AUCTION or listing.status != ListingStatus.APPROVED:
        return None

    auction = Auction.objects.filter(listing=listing).first()
    if auction:
        if auction.status != AuctionStatus.ACTIVE:
            auction.status = AuctionStatus.ACTIVE
            auction.save(update_fields=["status", "updated_at"])
        return auction

    starting_price = Decimal("10.00")
    min_bid = Decimal("5.00")
    buy_now_price = None

    if isinstance(listing.metadata, dict):
        if listing.metadata.get("starting_price"):
            try:
                starting_price = Decimal(str(listing.metadata["starting_price"]))
            except Exception:
                pass
        if listing.metadata.get("min_bid"):
            try:
                min_bid = Decimal(str(listing.metadata["min_bid"]))
            except Exception:
                pass
        bn = listing.metadata.get("buy_now_price") or listing.metadata.get("buynow_price") or listing.metadata.get("auto_accept_price")
        if bn:
            try:
                bn_val = Decimal(str(bn))
                if bn_val > starting_price:
                    buy_now_price = bn_val
            except Exception:
                pass

    now = timezone.now()
    days = 3
    if isinstance(listing.metadata, dict) and listing.metadata.get("auction_days"):
        try:
            days = int(listing.metadata["auction_days"])
        except Exception:
            pass

    return Auction.objects.create(
        tenant=listing.tenant,
        listing=listing,
        starting_price=starting_price,
        bid_increment=min_bid,
        current_price=Decimal("0.00"),
        buy_now_price=buy_now_price,
        start_at=now - timezone.timedelta(minutes=5),
        end_at=now + timezone.timedelta(days=days),
        status=AuctionStatus.ACTIVE,
    )


def approve_listing(listing: Listing, approved_by: Optional[str] = None) -> Listing:
    """Approves a pending listing for operational scheduling and activates an auction if applicable."""
    if listing.status != ListingStatus.PENDING:
        raise ValidationError(f"Cannot approve listing with status '{listing.status}'. Must be PENDING.")
    listing.status = ListingStatus.APPROVED
    if approved_by and isinstance(listing.metadata, dict):
        listing.metadata["approved_by"] = approved_by
        listing.save(update_fields=["status", "metadata", "updated_at"])
    else:
        listing.save(update_fields=["status", "updated_at"])

    # If this is an AUCTION listing, automatically create & activate the Auction
    if listing.listing_type == ListingType.AUCTION:
        ensure_active_auction_for_listing(listing)

    _notify_seller(
        listing,
        f"🎉 <b>Listing Approved!</b>\n\n"
        f"Your listing <b>{listing.title}</b> (#{listing.id}) has been accepted by administrators.\n"
        f"It is now ready for active auction scheduling."
    )
    return listing


def reject_listing(listing: Listing, reason: Optional[str] = None) -> Listing:
    """Rejects a pending listing and notifies the seller with the rejection reason."""
    if listing.status != ListingStatus.PENDING:
        raise ValidationError(f"Cannot reject listing with status '{listing.status}'. Must be PENDING.")
    listing.status = ListingStatus.REJECTED
    if reason and isinstance(listing.metadata, dict):
        listing.metadata["rejection_reason"] = reason
        listing.save(update_fields=["status", "metadata", "updated_at"])
    else:
        listing.save(update_fields=["status", "updated_at"])

    reason_text = reason or "Does not meet listing guidelines."
    _notify_seller(
        listing,
        f"⚠️ <b>Listing Not Approved</b>\n\n"
        f"Your listing <b>{listing.title}</b> (#{listing.id}) was not approved.\n\n"
        f"<b>Reason:</b> {reason_text}\n\n"
        f"You may update your listing details and submit again."
    )
    return listing


def close_listing(listing: Listing) -> Listing:
    """Marks a listing as closed."""
    listing.status = ListingStatus.CLOSED
    listing.save(update_fields=["status", "updated_at"])
    return listing

