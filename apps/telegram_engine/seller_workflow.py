"""Seller onboarding and listing creation state machine workflow for Telegram Bot.

Implements an explicit, persistent, tenant-isolated state machine for:
1. Seller registration questionnaire
2. Multi-step listing creation wizard
3. Photo/media uploads and secure tenant storage
4. Draft review and submission to PENDING_REVIEW
5. Recovery of interrupted listing drafts
"""
import os
import uuid
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.utils.text import slugify

from apps.tenants.models import Tenant
from apps.telegram_engine.models import (
    BotType,
    ConversationState,
    TelegramBotConfig,
    TelegramConversationState,
    TelegramUser,
)
from apps.listings.models import Listing, ListingImage, ListingStatus, ListingType, Seller, SellerStatus
from services.listings import create_listing, attach_listing_image
from services.telegram import TelegramService

logger = logging.getLogger(__name__)


# Standard categories for quick selection
POPULAR_CATEGORIES = ["Betta", "Discus", "Arowana", "Goldfish", "Guppy", "Shrimp", "Plants", "General"]


class SellerWorkflow:
    """Manages the lifecycle of Telegram-based seller interactions."""

    def __init__(
        self,
        tenant: Tenant,
        bot_config: TelegramBotConfig,
        telegram_service: Optional[TelegramService] = None,
    ):
        self.tenant = tenant
        self.bot_config = bot_config
        self.telegram_service = telegram_service or TelegramService(bot_config)

    def get_or_create_state(self, user: TelegramUser) -> TelegramConversationState:
        """Fetch or initialize persistent conversation state for this user within this tenant."""
        state_obj, _ = TelegramConversationState.objects.get_or_create(
            tenant=self.tenant,
            telegram_user=user,
            bot_type=self.bot_config.bot_type,
            defaults={"state": ConversationState.IDLE, "step": "", "context_data": {}},
        )
        return state_obj

    def get_seller_profile(self, user: TelegramUser) -> Optional[Seller]:
        """Fetch existing seller profile for user, if registered."""
        return Seller.objects.filter(tenant=self.tenant, telegram_user=user).first()

    # -------------------------------------------------------------------------
    # Entry Point: /start Command Handler
    # -------------------------------------------------------------------------

    def handle_start(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Handle /start command with recovery check for in-progress workflows."""
        conv = self.get_or_create_state(user)
        seller = self.get_seller_profile(user)

        # Check for recoverable in-progress listing draft
        if conv.state in (ConversationState.CREATING_LISTING, ConversationState.REVIEWING_LISTING):
            draft_title = conv.context_data.get("title") or "Untitled Draft"
            keyboard = {
                "inline_keyboard": [
                    [{"text": "▶️ Continue Listing", "callback_data": "resume_listing"}],
                    [{"text": "🗑️ Discard Draft", "callback_data": "cancel_listing"}],
                ]
            }
            text = (
                f"⚠️ <b>Incomplete Listing Found</b>\n\n"
                f"You have an unfinished listing in progress:\n"
                f"<b>{draft_title}</b> (Current Step: {conv.step})\n\n"
                "Would you like to resume where you left off or discard it?"
            )
            self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            return {"handled": True, "action": "prompt_resume_draft"}

        # If user is not yet a registered seller
        if not seller:
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📝 Register as Seller", "callback_data": "seller_register"}],
                    [{"text": "❓ Help & Guidelines", "callback_data": "seller_help"}],
                ]
            }
            text = (
                f"👋 <b>Welcome to {self.tenant.name} Seller Bot!</b>\n\n"
                f"Official Currency: <b>{self.tenant.currency}</b>\n\n"
                "To list items and auction fish lots on our platform, you must first register your seller profile.\n\n"
                "Click below to begin your onboarding questionnaire."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            return {"handled": True, "action": "prompt_registration"}

        # User is an active registered seller
        keyboard = {
            "inline_keyboard": [
                [{"text": "➕ Create New Listing", "callback_data": "create_listing"}],
                [{"text": "📋 My Listings", "callback_data": "my_listings"}],
                [{"text": "👤 My Profile", "callback_data": "my_profile"}],
                [{"text": "❓ Help & Guidelines", "callback_data": "seller_help"}],
            ]
        }
        text = (
            f"🐟 <b>Welcome back, {seller.business_name}!</b>\n\n"
            f"<b>Seller Account:</b> ACTIVE\n"
            f"<b>Platform:</b> {self.tenant.name} ({self.tenant.currency})\n\n"
            "What would you like to do today?"
        )
        self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return {"handled": True, "action": "seller_dashboard"}

    # -------------------------------------------------------------------------
    # Seller Registration Questionnaire
    # -------------------------------------------------------------------------

    def start_registration(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Initiate the seller registration questionnaire."""
        seller = self.get_seller_profile(user)
        if seller:
            text = f"You are already registered as an approved seller: <b>{seller.business_name}</b>."
            self.telegram_service.send_message(chat_id=chat_id, text=text)
            return self.handle_start(user, chat_id)

        conv = self.get_or_create_state(user)
        conv.set_state(
            ConversationState.REGISTERING,
            step="BUSINESS_NAME",
            context_update={"reg_data": {}},
        )

        text = (
            "📝 <b>Seller Registration — Step 1 of 5</b>\n\n"
            "Please enter your <b>Business / Farm / Brand Name</b>:\n"
            "<i>(Example: 'Royal Betta Malaysia' or 'John Aquatics')</i>"
        )
        self.telegram_service.send_message(chat_id=chat_id, text=text)
        return {"handled": True, "step": "BUSINESS_NAME"}

    def handle_registration_input(self, user: TelegramUser, chat_id: int, text: str) -> Dict[str, Any]:
        """Progress through registration questionnaire steps."""
        conv = self.get_or_create_state(user)
        clean_text = text.strip()
        reg_data = conv.context_data.get("reg_data", {})

        if conv.step == "BUSINESS_NAME":
            if len(clean_text) < 2 or len(clean_text) > 120:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Business name must be between 2 and 120 characters. Please re-enter:",
                )
                return {"handled": True, "error": "invalid_length"}
            reg_data["business_name"] = clean_text
            conv.set_state(ConversationState.REGISTERING, step="CONTACT_NAME", context_update={"reg_data": reg_data})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📝 <b>Step 2 of 5: Contact Person</b>\n\n"
                    "Please enter the <b>Full Name</b> of the primary contact person:"
                ),
            )
            return {"handled": True, "step": "CONTACT_NAME"}

        elif conv.step == "CONTACT_NAME":
            if len(clean_text) < 2 or len(clean_text) > 120:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Contact name must be between 2 and 120 characters. Please re-enter:",
                )
                return {"handled": True, "error": "invalid_length"}
            reg_data["contact_name"] = clean_text
            conv.set_state(ConversationState.REGISTERING, step="PHONE", context_update={"reg_data": reg_data})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📞 <b>Step 3 of 5: Phone Number</b>\n\n"
                    "Please enter your <b>WhatsApp / Mobile Phone Number</b>\n"
                    "<i>(Example: +60123456789)</i>:"
                ),
            )
            return {"handled": True, "step": "PHONE"}

        elif conv.step == "PHONE":
            if len(clean_text) < 7 or len(clean_text) > 25:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Please enter a valid telephone number (7-25 digits/symbols):",
                )
                return {"handled": True, "error": "invalid_phone"}
            reg_data["phone"] = clean_text
            conv.set_state(ConversationState.REGISTERING, step="EMAIL", context_update={"reg_data": reg_data})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📧 <b>Step 4 of 5: Email Address</b>\n\n"
                    "Please enter your <b>Email Address</b>\n"
                    "<i>(or type <b>'skip'</b> if you do not have one)</i>:"
                ),
            )
            return {"handled": True, "step": "EMAIL"}

        elif conv.step == "EMAIL":
            if clean_text.lower() == "skip":
                reg_data["email"] = ""
            elif "@" not in clean_text or "." not in clean_text or len(clean_text) > 100:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Invalid email format. Please enter a valid email or type <b>'skip'</b>:",
                )
                return {"handled": True, "error": "invalid_email"}
            else:
                reg_data["email"] = clean_text

            conv.set_state(ConversationState.REGISTERING, step="ADDRESS", context_update={"reg_data": reg_data})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📍 <b>Step 5 of 5: Farm Location / Address</b>\n\n"
                    "Please enter your <b>City, Region, or Dispatch Location</b>\n"
                    "<i>(Example: 'Johor Bahru, Johor' or full address)</i>:"
                ),
            )
            return {"handled": True, "step": "ADDRESS"}

        elif conv.step == "ADDRESS":
            if len(clean_text) < 2 or len(clean_text) > 300:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Location must be between 2 and 300 characters. Please re-enter:",
                )
                return {"handled": True, "error": "invalid_address"}
            reg_data["address"] = clean_text

            # Finalize Seller Creation
            seller = Seller.objects.create(
                tenant=self.tenant,
                telegram_user=user,
                seller_id=str(user.telegram_user_id),
                business_name=reg_data["business_name"],
                contact_name=reg_data["contact_name"],
                phone=reg_data["phone"],
                email=reg_data.get("email", ""),
                address=reg_data["address"],
                status=SellerStatus.ACTIVE,
            )

            conv.reset()

            keyboard = {
                "inline_keyboard": [
                    [{"text": "➕ Create First Listing", "callback_data": "create_listing"}],
                    [{"text": "👤 View My Profile", "callback_data": "my_profile"}],
                ]
            }
            welcome_text = (
                f"🎉 <b>Registration Complete!</b>\n\n"
                f"Welcome to {self.tenant.name}, <b>{seller.business_name}</b>!\n"
                f"Your seller account is approved and active.\n\n"
                f"• <b>Contact:</b> {seller.contact_name}\n"
                f"• <b>Phone:</b> {seller.phone}\n"
                f"• <b>Location:</b> {seller.address}\n\n"
                "You can now submit items for live auctions."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=welcome_text, reply_markup=keyboard)
            return {"handled": True, "seller_id": seller.seller_id}

        return {"handled": False, "reason": "unhandled_registration_step"}

    # -------------------------------------------------------------------------
    # Listing Creation Wizard
    # -------------------------------------------------------------------------

    def start_listing_wizard(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Start the multi-step listing creation wizard for a verified seller."""
        seller = self.get_seller_profile(user)
        if not seller or seller.status != SellerStatus.ACTIVE:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ You must complete seller registration before creating a listing. Send /start to begin.",
            )
            return {"handled": True, "error": "unauthorized_seller"}

        conv = self.get_or_create_state(user)
        conv.set_state(
            ConversationState.CREATING_LISTING,
            step="TITLE",
            context_update={
                "listing_draft": {
                    "seller_id": seller.seller_id,
                    "images": [],
                }
            },
        )

        cancel_keyboard = {
            "inline_keyboard": [
                [{"text": "❌ Cancel Listing", "callback_data": "cancel_listing"}]
            ]
        }
        text = (
            "📝 <b>Create New Listing (Step 1/6)</b>\n\n"
            "Please enter the <b>Listing Title</b>:\n"
            "<i>(Example: 'Super Red Dragon Guppy Trio' or 'Grade AAA Kohaku 25cm')</i>"
        )
        self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=cancel_keyboard)
        return {"handled": True, "step": "TITLE"}

    def handle_listing_input(self, user: TelegramUser, chat_id: int, text: str) -> Dict[str, Any]:
        """Progress through listing wizard fields."""
        conv = self.get_or_create_state(user)
        clean_text = text.strip()
        draft = conv.context_data.get("listing_draft", {})

        cancel_keyboard = {
            "inline_keyboard": [
                [{"text": "❌ Cancel Listing", "callback_data": "cancel_listing"}]
            ]
        }

        if conv.step == "TITLE":
            if len(clean_text) < 3 or len(clean_text) > 180:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Title must be between 3 and 180 characters. Please re-enter title:",
                    reply_markup=cancel_keyboard,
                )
                return {"handled": True, "error": "invalid_title"}
            draft["title"] = clean_text
            conv.set_state(ConversationState.CREATING_LISTING, step="DESCRIPTION", context_update={"listing_draft": draft})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📝 <b>Step 2/6: Description</b>\n\n"
                    "Please provide details about the item\n"
                    "<i>(Size, age, feeding habits, shipping details, or special terms)</i>:"
                ),
                reply_markup=cancel_keyboard,
            )
            return {"handled": True, "step": "DESCRIPTION"}

        elif conv.step == "DESCRIPTION":
            if len(clean_text) < 5:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Description is too short. Please provide at least 5 characters:",
                    reply_markup=cancel_keyboard,
                )
                return {"handled": True, "error": "short_description"}
            draft["description"] = clean_text
            conv.set_state(ConversationState.CREATING_LISTING, step="CATEGORY", context_update={"listing_draft": draft})

            # Present inline category quick buttons
            cat_buttons = []
            row = []
            for cat in POPULAR_CATEGORIES:
                row.append({"text": cat, "callback_data": f"cat_select:{cat}"})
                if len(row) == 2:
                    cat_buttons.append(row)
                    row = []
            if row:
                cat_buttons.append(row)
            cat_buttons.append([{"text": "❌ Cancel Listing", "callback_data": "cancel_listing"}])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "🏷️ <b>Step 3/6: Category</b>\n\n"
                    "Select a category below or type your custom category name:"
                ),
                reply_markup={"inline_keyboard": cat_buttons},
            )
            return {"handled": True, "step": "CATEGORY"}

        elif conv.step == "CATEGORY":
            draft["category"] = clean_text[:80]
            conv.set_state(ConversationState.CREATING_LISTING, step="STARTING_PRICE", context_update={"listing_draft": draft})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    f"💰 <b>Step 4/6: Starting Price</b>\n\n"
                    f"Enter starting bid amount in <b>{self.tenant.currency}</b> (e.g. 50.00):"
                ),
                reply_markup=cancel_keyboard,
            )
            return {"handled": True, "step": "STARTING_PRICE"}

        elif conv.step == "STARTING_PRICE":
            try:
                price_val = Decimal(clean_text.replace(",", "").replace("$", ""))
                if price_val <= Decimal("0.00"):
                    raise InvalidOperation()
            except (InvalidOperation, ValueError):
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=f"❌ Invalid amount. Enter a positive number in {self.tenant.currency} (e.g. 50.00):",
                    reply_markup=cancel_keyboard,
                )
                return {"handled": True, "error": "invalid_price"}

            draft["starting_price"] = str(price_val)
            conv.set_state(ConversationState.CREATING_LISTING, step="BUY_NOW_PRICE", context_update={"listing_draft": draft})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    f"⚡ <b>Step 5/6: Buy-It-Now Price (Optional)</b>\n\n"
                    f"Enter the instant purchase price in <b>{self.tenant.currency}</b>,\n"
                    f"or type <b>'0'</b> to disable Buy-It-Now:"
                ),
                reply_markup=cancel_keyboard,
            )
            return {"handled": True, "step": "BUY_NOW_PRICE"}

        elif conv.step == "BUY_NOW_PRICE":
            try:
                bn_val = Decimal(clean_text.replace(",", "").replace("$", ""))
                if bn_val < Decimal("0.00"):
                    raise InvalidOperation()
            except (InvalidOperation, ValueError):
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=f"❌ Invalid amount. Enter 0 to skip or a valid amount in {self.tenant.currency}:",
                    reply_markup=cancel_keyboard,
                )
                return {"handled": True, "error": "invalid_buy_now_price"}

            draft["buy_now_price"] = str(bn_val) if bn_val > 0 else ""
            conv.set_state(ConversationState.CREATING_LISTING, step="QUANTITY", context_update={"listing_draft": draft})
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📦 <b>Step 6/6: Available Quantity</b>\n\n"
                    "Enter the total units available (default 1):"
                ),
                reply_markup=cancel_keyboard,
            )
            return {"handled": True, "step": "QUANTITY"}

        elif conv.step == "QUANTITY":
            try:
                qty_val = int(clean_text)
                if qty_val < 1 or qty_val > 1000:
                    raise ValueError()
            except ValueError:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text="❌ Please enter a valid quantity between 1 and 1000:",
                    reply_markup=cancel_keyboard,
                )
                return {"handled": True, "error": "invalid_quantity"}

            draft["quantity"] = qty_val
            conv.set_state(ConversationState.CREATING_LISTING, step="IMAGES", context_update={"listing_draft": draft})

            images_keyboard = {
                "inline_keyboard": [
                    [{"text": "✅ Done Uploading Photos", "callback_data": "listing_images_done"}],
                    [{"text": "❌ Cancel Listing", "callback_data": "cancel_listing"}],
                ]
            }
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=(
                    "📸 <b>Upload Photos</b>\n\n"
                    "Send one or more photos of your item now.\n"
                    "When finished, click <b>[Done Uploading Photos]</b> below:"
                ),
                reply_markup=images_keyboard,
            )
            return {"handled": True, "step": "IMAGES"}

        return {"handled": False, "reason": "unhandled_listing_step"}

    # -------------------------------------------------------------------------
    # Photo/Media Upload Handling
    # -------------------------------------------------------------------------

    def handle_photo_upload(self, user: TelegramUser, chat_id: int, photo_array: list) -> Dict[str, Any]:
        """Download uploaded photo and attach to current in-progress draft."""
        conv = self.get_or_create_state(user)
        if conv.state != ConversationState.CREATING_LISTING:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="ℹ️ Photos can only be received while creating a listing. Type /start to open the menu.",
            )
            return {"handled": True, "ignored": True}

        # Select highest resolution photo in array
        best_photo = photo_array[-1]
        file_id = best_photo.get("file_id")

        draft = conv.context_data.get("listing_draft", {})
        images_list = draft.setdefault("images", [])

        # Prevent runaway photo spam
        if len(images_list) >= 10:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="⚠️ Maximum of 10 photos reached. Please click <b>[Done Uploading Photos]</b> to proceed.",
            )
            return {"handled": True, "limit_reached": True}

        # Download Telegram photo securely
        local_rel_path = ""
        try:
            file_meta = self.telegram_service.get_file(file_id)
            if file_meta.get("ok"):
                tg_file_path = file_meta.get("result", {}).get("file_path", "")
                ext = os.path.splitext(tg_file_path)[1] or ".jpg"
                unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
                dest_dir = os.path.join(settings.MEDIA_ROOT, "tenants", self.tenant.slug, "listings")
                dest_path = os.path.join(dest_dir, unique_name)
                self.telegram_service.download_file(tg_file_path, dest_path)
                local_rel_path = f"tenants/{self.tenant.slug}/listings/{unique_name}"
        except Exception as exc:
            logger.warning("Could not download Telegram photo %s: %s", file_id, exc)

        images_list.append({
            "telegram_file_id": file_id,
            "local_path": local_rel_path,
        })
        draft["images"] = images_list
        conv.set_state(conv.state, step=conv.step, context_update={"listing_draft": draft})

        count = len(images_list)
        keyboard = {
            "inline_keyboard": [
                [{"text": f"✅ Done ({count} photo{'s' if count > 1 else ''})", "callback_data": "listing_images_done"}],
                [{"text": "❌ Cancel Listing", "callback_data": "cancel_listing"}],
            ]
        }
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=f"📷 Photo #{count} received! Send another photo or click <b>[Done]</b> when finished.",
            reply_markup=keyboard,
        )
        return {"handled": True, "photo_count": count}

    # -------------------------------------------------------------------------
    # Review & Submission
    # -------------------------------------------------------------------------

    def show_review_summary(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Show draft summary screen with Submit and Cancel options."""
        conv = self.get_or_create_state(user)
        draft = conv.context_data.get("listing_draft", {})

        conv.set_state(ConversationState.REVIEWING_LISTING, step="REVIEW")

        title = draft.get("title", "Untitled")
        category = draft.get("category", "General")
        desc = draft.get("description", "No description provided.")
        st_price = draft.get("starting_price", "0.00")
        bn_price = draft.get("buy_now_price")
        bn_display = f"{self.tenant.currency} {bn_price}" if bn_price else "Disabled"
        qty = draft.get("quantity", 1)
        photos_count = len(draft.get("images", []))

        keyboard = {
            "inline_keyboard": [
                [{"text": "🚀 Submit Listing for Approval", "callback_data": "submit_listing"}],
                [{"text": "❌ Cancel & Discard", "callback_data": "cancel_listing"}],
            ]
        }

        review_text = (
            "📋 <b>Review Your Listing Summary</b>\n\n"
            f"• <b>Title:</b> {title}\n"
            f"• <b>Category:</b> {category}\n"
            f"• <b>Starting Bid:</b> {self.tenant.currency} {st_price}\n"
            f"• <b>Buy-It-Now:</b> {bn_display}\n"
            f"• <b>Quantity:</b> {qty}\n"
            f"• <b>Photos Attached:</b> {photos_count}\n\n"
            f"<b>Description:</b>\n{desc}\n\n"
            "<i>Once submitted, administrators will review your item for live auction placement.</i>"
        )
        self.telegram_service.send_message(chat_id=chat_id, text=review_text, reply_markup=keyboard)
        return {"handled": True, "action": "show_review"}

    def submit_final_listing(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Finalize draft, validate server-side, create DB Listing and images."""
        conv = self.get_or_create_state(user)
        draft = conv.context_data.get("listing_draft", {})
        seller = self.get_seller_profile(user)

        if not seller or not draft.get("title"):
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ Could not submit listing. Session expired or draft is empty. Send /start to begin.",
            )
            conv.reset()
            return {"handled": True, "error": "empty_draft"}

        # Server-side validation
        title = draft.get("title", "").strip()
        description = draft.get("description", "").strip()
        category = draft.get("category", "General").strip()
        starting_price = Decimal(draft.get("starting_price", "0.00"))
        buy_now_price = Decimal(draft.get("buy_now_price")) if draft.get("buy_now_price") else None
        quantity = int(draft.get("quantity", 1))

        metadata = {
            "starting_price": str(starting_price),
            "buy_now_price": str(buy_now_price) if buy_now_price else None,
            "seller_business_name": seller.business_name,
            "seller_contact_name": seller.contact_name,
            "seller_phone": seller.phone,
        }

        listing = create_listing(
            tenant=self.tenant,
            seller_id=seller.seller_id,
            seller_username=user.username or seller.business_name,
            title=title,
            description=description,
            category=category,
            listing_type=ListingType.AUCTION,
            quantity=quantity,
            metadata=metadata,
            status=ListingStatus.PENDING,
        )

        # Attach images
        for idx, img_info in enumerate(draft.get("images", [])):
            attach_listing_image(
                listing=listing,
                file_url=img_info.get("local_path", ""),
                telegram_file_id=img_info.get("telegram_file_id", ""),
                order=idx,
            )

        # Reset conversation state
        conv.reset()

        keyboard = {
            "inline_keyboard": [
                [{"text": "➕ Create Another Listing", "callback_data": "create_listing"}],
                [{"text": "📋 My Listings", "callback_data": "my_listings"}],
            ]
        }
        success_text = (
            f"🎉 <b>Listing Submitted Successfully!</b>\n\n"
            f"Your item <b>{listing.title}</b> (#{listing.id}) is now <b>PENDING REVIEW</b>.\n"
            "Our moderators have been notified. You will receive an instant notification here when it is approved."
        )
        self.telegram_service.send_message(chat_id=chat_id, text=success_text, reply_markup=keyboard)
        return {"handled": True, "listing_id": listing.id}

    # -------------------------------------------------------------------------
    # Helper & Profile Views
    # -------------------------------------------------------------------------

    def show_my_profile(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Display registered seller profile."""
        seller = self.get_seller_profile(user)
        if not seller:
            return self.handle_start(user, chat_id)

        keyboard = {
            "inline_keyboard": [
                [{"text": "➕ Create Listing", "callback_data": "create_listing"}],
                [{"text": "📋 My Listings", "callback_data": "my_listings"}],
                [{"text": "🏠 Main Menu", "callback_data": "seller_start"}],
            ]
        }
        text = (
            f"👤 <b>Seller Profile: {seller.business_name}</b>\n\n"
            f"• <b>Status:</b> {seller.status}\n"
            f"• <b>Contact Person:</b> {seller.contact_name}\n"
            f"• <b>Phone:</b> {seller.phone}\n"
            f"• <b>Email:</b> {seller.email or 'None'}\n"
            f"• <b>Address / Location:</b> {seller.address}\n"
            f"• <b>Platform Tenant:</b> {self.tenant.name} ({self.tenant.currency})\n"
        )
        self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return {"handled": True, "action": "my_profile"}

    def show_my_listings(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Display recent listings submitted by this seller."""
        seller = self.get_seller_profile(user)
        if not seller:
            return self.handle_start(user, chat_id)

        listings = Listing.objects.filter(tenant=self.tenant, seller_id=seller.seller_id).order_by("-created_at")[:10]

        if not listings.exists():
            keyboard = {
                "inline_keyboard": [
                    [{"text": "➕ Create Your First Listing", "callback_data": "create_listing"}],
                ]
            }
            text = "📋 You have not created any listings yet. Click below to submit your first item!"
            self.telegram_service.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            return {"handled": True, "action": "empty_listings"}

        lines = ["📋 <b>Your Recent Listings:</b>\n"]
        for item in listings:
            status_emoji = {
                ListingStatus.APPROVED: "✅",
                ListingStatus.PENDING: "⏳",
                ListingStatus.REJECTED: "❌",
                ListingStatus.CLOSED: "🔒",
                ListingStatus.DRAFT: "📝",
            }.get(item.status, "•")
            lines.append(f"{status_emoji} <b>#{item.id}</b> {item.title} — <i>{item.status}</i>")

        keyboard = {
            "inline_keyboard": [
                [{"text": "➕ Create New Listing", "callback_data": "create_listing"}],
                [{"text": "🏠 Main Menu", "callback_data": "seller_start"}],
            ]
        }
        self.telegram_service.send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=keyboard)
        return {"handled": True, "action": "show_listings", "count": listings.count()}

    # -------------------------------------------------------------------------
    # Central Action & Callback Router
    # -------------------------------------------------------------------------

    def handle_callback(self, user: TelegramUser, chat_id: int, callback_data: str) -> Dict[str, Any]:
        """Route incoming callback query payloads."""
        if callback_data == "seller_register":
            return self.start_registration(user, chat_id)

        elif callback_data == "seller_help":
            help_text = (
                f"ℹ️ <b>{self.tenant.name} Seller Help & Guidelines</b>\n\n"
                "• All items must comply with local wildlife and dispatch regulations.\n"
                "• Provide clear, unaltered photos of the actual fish/item.\n"
                "• All currency figures are denominated in <b>"
                f"{self.tenant.currency}</b>.\n"
                "• Once submitted, listings undergo quick administrator verification.\n\n"
                "Type /start anytime to return to the main menu."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=help_text)
            return {"handled": True, "action": "help"}

        elif callback_data == "create_listing":
            return self.start_listing_wizard(user, chat_id)

        elif callback_data.startswith("cat_select:"):
            chosen_cat = callback_data.split(":", 1)[1]
            return self.handle_listing_input(user, chat_id, chosen_cat)

        elif callback_data == "listing_images_done":
            return self.show_review_summary(user, chat_id)

        elif callback_data == "submit_listing":
            return self.submit_final_listing(user, chat_id)

        elif callback_data == "cancel_listing":
            conv = self.get_or_create_state(user)
            conv.reset()
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="❌ Listing cancelled. Draft cleared.",
            )
            return self.handle_start(user, chat_id)

        elif callback_data == "resume_listing":
            conv = self.get_or_create_state(user)
            if conv.state == ConversationState.REVIEWING_LISTING:
                return self.show_review_summary(user, chat_id)
            else:
                draft = conv.context_data.get("listing_draft", {})
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=f"▶️ Resuming draft: <b>{draft.get('title', 'Untitled')}</b>.\nPlease continue with step: <b>{conv.step}</b>.",
                )
                return {"handled": True, "action": "resumed", "step": conv.step}

        elif callback_data == "my_profile":
            return self.show_my_profile(user, chat_id)

        elif callback_data == "my_listings":
            return self.show_my_listings(user, chat_id)

        elif callback_data == "seller_start":
            return self.handle_start(user, chat_id)

        return {"handled": False, "reason": f"unknown_callback_{callback_data}"}
