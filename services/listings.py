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


def approve_listing(listing: Listing, approved_by: Optional[str] = None) -> Listing:
    """Approves a pending listing for operational scheduling and notifies the seller."""
    if listing.status != ListingStatus.PENDING:
        raise ValidationError(f"Cannot approve listing with status '{listing.status}'. Must be PENDING.")
    listing.status = ListingStatus.APPROVED
    if approved_by and isinstance(listing.metadata, dict):
        listing.metadata["approved_by"] = approved_by
        listing.save(update_fields=["status", "metadata", "updated_at"])
    else:
        listing.save(update_fields=["status", "updated_at"])

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

