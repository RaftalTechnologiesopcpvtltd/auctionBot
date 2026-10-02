"""Seller Telegram Bot workflow replicating exact legacy UX from fish_registration.py.

Underneath the identical legacy Telegram UX:
- Multi-tenant tenant scoping and server-side authorization.
- Phase 03 Listing & Media domain models.
- Phase 06 financial ledger for wallet and balances.
- Persistent recoverable conversation states via TelegramConversationState.
"""
import logging
import os
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import localtime

from apps.listings.models import Listing, ListingImage, ListingStatus, ListingType, Seller, SellerStatus
from apps.telegram_engine.keyboards import BREED_OPTIONS, SellerKeyboards
from apps.telegram_engine.messages import SellerMessages
from apps.telegram_engine.models import TelegramBotConfig, TelegramConversationState, TelegramUser
from apps.tenants.models import Tenant
from services.listings import attach_listing_image, create_listing
from services.telegram import TelegramService

logger = logging.getLogger(__name__)


class SellerWorkflowStep:
    IDLE = "IDLE"
    WAITING_PASSWORD = "WAITING_PASSWORD"
    BREED = "BREED"
    TITLE = "TITLE"
    DESCRIPTION = "DESCRIPTION"
    QUANTITY = "QUANTITY"
    CONTACT = "CONTACT"
    CATEGORY = "CATEGORY"
    AUCTION_STARTINGPRICE = "AUCTION_STARTINGPRICE"
    AUCTION_AUTO_ACCEPT = "AUCTION_AUTO_ACCEPT"
    AUCTION_MIN_BID = "AUCTION_MIN_BID"
    AUCTION_START_DATE = "AUCTION_START_DATE"
    AUCTION_START_TIME = "AUCTION_START_TIME"
    AUCTION_END_TIME = "AUCTION_END_TIME"
    BUYNOW_PRICE = "BUYNOW_PRICE"
    PICTURE = "PICTURE"
    VIDEO = "VIDEO"
    WALLET_AMOUNT = "WALLET_AMOUNT"
    GET_PAYMENT_DETAILS = "GET_PAYMENT_DETAILS"
    CONFIRM_PAYMENT = "CONFIRM_PAYMENT"


class SellerWorkflow:
    """Manages seller interactions, listing wizard, and seller wallet."""

    def __init__(
        self,
        tenant: Tenant,
        bot_config: TelegramBotConfig,
        telegram_service: Optional[TelegramService] = None,
    ):
        self.tenant = tenant
        self.bot_config = bot_config
        self.telegram_service = telegram_service or TelegramService(bot_config=bot_config)

    def _get_conversation_state(self, user: TelegramUser) -> TelegramConversationState:
        """Fetch or initialize persistent conversation state for this seller within this tenant."""
        state, _ = TelegramConversationState.objects.get_or_create(
            tenant=self.tenant,
            telegram_user=user,
            bot_type=self.bot_config.bot_type,
            defaults={"state": "IDLE", "step": SellerWorkflowStep.IDLE, "context_data": {}},
        )
        return state

    def handle_start(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Exact legacy /start behavior for seller bot."""
        # 1. Check if user is blocked
        if user.is_blocked:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.BLOCKED_USER,
            )
            return {"handled": True, "action": "blocked"}

        # 2. Check if password authentication is required
        if self.bot_config.require_password and not user.is_authorized:
            conv = self._get_conversation_state(user)
            conv.state = "WAITING_PASSWORD"
            conv.step = SellerWorkflowStep.WAITING_PASSWORD
            conv.save(update_fields=["state", "step", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.PASSWORD_PROMPT,
            )
            return {"handled": True, "action": "prompt_password"}

        # 3. Authorized or no password required
        conv = self._get_conversation_state(user)
        conv.state = "IDLE"
        conv.step = SellerWorkflowStep.IDLE
        conv.save(update_fields=["state", "step", "updated_at"])

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.WELCOME_CHOOSE_OPTION,
            reply_markup=SellerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "main_menu"}

    def handle_text(self, user: TelegramUser, chat_id: int, text: str) -> Dict[str, Any]:
        """Routes text messages and listing creation wizard answers."""
        text_clean = text.strip()

        # Handle cancel command
        if text_clean == "/cancel":
            return self.handle_cancel(user, chat_id)

        conv = self._get_conversation_state(user)

        # 1. Password check
        if conv.step == SellerWorkflowStep.WAITING_PASSWORD:
            if text_clean == self.bot_config.access_password:
                user.is_authorized = True
                user.save(update_fields=["is_authorized"])
                conv.state = "IDLE"
                conv.step = SellerWorkflowStep.IDLE
                conv.save(update_fields=["state", "step", "updated_at"])

                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.PASSWORD_AUTHORIZED,
                    reply_markup=SellerKeyboards.main_menu(),
                )
                return {"handled": True, "action": "password_authorized"}
            else:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.PASSWORD_INCORRECT,
                )
                return {"handled": True, "action": "password_incorrect"}

        # 2. Main Menu Actions
        if text_clean == "Start New Listing":
            return self.start_new_listing(user, chat_id)

        elif text_clean == "My Listings":
            return self._handle_my_listings(user, chat_id)

        elif text_clean == "Live Listings":
            base_url = getattr(settings, "BASE_SITE_URL", "https://auctionbot.shop")
            msg = f"View your live listings at:\n{base_url}/live-listing/?seller_id={user.telegram_user_id}"
            self.telegram_service.send_message(chat_id=chat_id, text=msg)
            return {"handled": True, "action": "live_listings"}

        elif text_clean == "My Closed Listings":
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.NO_LISTINGS_AVAILABLE,
            )
            return {"handled": True, "action": "my_closed_listings"}

        elif text_clean == "Auction Ending Soon":
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.NO_LISTINGS_AVAILABLE,
            )
            return {"handled": True, "action": "auction_ending_soon"}

        elif text_clean == "Sold Items":
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.NO_SOLD_LISTINGS,
            )
            return {"handled": True, "action": "sold_items"}

        elif text_clean == "My Wallet":
            from apps.finance.models import FinancialAccount, AccountType
            account = FinancialAccount.objects.filter(
                tenant=self.tenant,
                owner_id=str(user.telegram_user_id),
                account_type=AccountType.USER_WALLET,
            ).first()
            balance = account.available_balance if account else Decimal("0.00")
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.WALLET_DETAILS_HEADER.format(balance=balance),
                reply_markup=SellerKeyboards.wallet_add_money(user.telegram_user_id),
            )
            return {"handled": True, "action": "my_wallet"}

        elif text_clean == "Helpdesk":
            helpdesk_details = (
                getattr(self.tenant, "metadata", {}).get("helpdesk_details")
                or f"Contact {self.tenant.name} support for assistance."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(helpdesk_details))
            return {"handled": True, "action": "helpdesk"}

        elif text_clean == "Join Group":
            group_details = (
                getattr(self.tenant, "metadata", {}).get("group_details")
                or f"Join our {self.tenant.name} Telegram Community!"
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(group_details))
            return {"handled": True, "action": "join_group"}

        elif text_clean == "About":
            about_us = (
                getattr(self.tenant, "metadata", {}).get("about_us")
                or f"About {self.tenant.name}."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(about_us))
            return {"handled": True, "action": "about"}

        # 3. Wizard Step Dispatch
        step = conv.step
        ctx = conv.context_data or {}

        if step == SellerWorkflowStep.TITLE:
            ctx["title"] = text_clean
            conv.step = SellerWorkflowStep.DESCRIPTION
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.PRODUCT_DESCRIPTION_PROMPT,
            )
            return {"handled": True, "action": "received_title"}

        elif step == SellerWorkflowStep.DESCRIPTION:
            ctx["description"] = text_clean
            conv.step = SellerWorkflowStep.QUANTITY
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.QUANTITY_PROMPT,
            )
            return {"handled": True, "action": "received_description"}

        elif step == SellerWorkflowStep.QUANTITY:
            if not text_clean.isdigit():
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.INVALID_QUANTITY,
                )
                return {"handled": True, "action": "invalid_quantity"}

            ctx["quantity"] = int(text_clean)
            conv.step = SellerWorkflowStep.CONTACT
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.CONTACT_PROMPT,
            )
            return {"handled": True, "action": "received_quantity"}

        elif step == SellerWorkflowStep.CONTACT:
            ctx["contact"] = text_clean
            conv.step = SellerWorkflowStep.CATEGORY
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.SELECT_LISTING_OPTION,
                reply_markup=SellerKeyboards.category_options(),
            )
            return {"handled": True, "action": "received_contact"}

        elif step == SellerWorkflowStep.AUCTION_STARTINGPRICE:
            if not text_clean.isdigit():
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.INVALID_PRICE,
                )
                return {"handled": True, "action": "invalid_price"}

            ctx["starting_price"] = int(text_clean)
            conv.step = SellerWorkflowStep.AUCTION_AUTO_ACCEPT
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.AUTO_ACCEPT_OFFER_PRICE_PROMPT,
            )
            return {"handled": True, "action": "received_starting_price"}

        elif step == SellerWorkflowStep.AUCTION_AUTO_ACCEPT:
            if not text_clean.isdigit():
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.INVALID_PRICE,
                )
                return {"handled": True, "action": "invalid_price"}

            ctx["auto_accept_price"] = int(text_clean)
            conv.step = SellerWorkflowStep.AUCTION_MIN_BID
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.MIN_BID_PROMPT,
            )
            return {"handled": True, "action": "received_auto_accept"}

        elif step == SellerWorkflowStep.AUCTION_MIN_BID:
            if not text_clean.isdigit():
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.INVALID_PRICE,
                )
                return {"handled": True, "action": "invalid_price"}

            ctx["min_bid"] = int(text_clean)
            conv.step = SellerWorkflowStep.AUCTION_START_DATE
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.START_DATE_PROMPT,
            )
            return {"handled": True, "action": "received_min_bid"}

        elif step == SellerWorkflowStep.AUCTION_START_DATE:
            ctx["start_date"] = text_clean
            conv.step = SellerWorkflowStep.AUCTION_START_TIME
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"Date selected: {text_clean}\n\n/cancel",
            )
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.START_TIME_PROMPT,
            )
            return {"handled": True, "action": "received_start_date"}

        elif step == SellerWorkflowStep.AUCTION_START_TIME:
            ctx["start_time"] = text_clean
            conv.step = SellerWorkflowStep.AUCTION_END_TIME
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.END_TIME_PROMPT,
                reply_markup=SellerKeyboards.end_time_presets(),
            )
            return {"handled": True, "action": "received_start_time"}

        elif step == SellerWorkflowStep.AUCTION_END_TIME:
            ctx["end_time"] = text_clean
            conv.step = SellerWorkflowStep.PICTURE
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_IMAGES_PROMPT,
            )
            return {"handled": True, "action": "received_end_time"}

        elif step == SellerWorkflowStep.BUYNOW_PRICE:
            if not text_clean.isdigit():
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.INVALID_PRICE,
                )
                return {"handled": True, "action": "invalid_price"}

            ctx["buynow_price"] = int(text_clean)
            conv.step = SellerWorkflowStep.PICTURE
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_IMAGES_PROMPT,
            )
            return {"handled": True, "action": "received_buynow_price"}

        elif step == SellerWorkflowStep.PICTURE:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.PLEASE_UPLOAD_PICTURE,
            )
            return {"handled": True, "action": "prompt_picture"}

        elif step == SellerWorkflowStep.GET_PAYMENT_DETAILS:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text="Please send a valid payment proof image.",
            )
            return {"handled": True, "action": "prompt_payment_proof_valid"}

        # Default fallback
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.WELCOME_CHOOSE_OPTION,
            reply_markup=SellerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "fallback"}

    def handle_cancel(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Exact legacy /cancel behavior."""
        conv = self._get_conversation_state(user)
        conv.state = "IDLE"
        conv.step = SellerWorkflowStep.IDLE
        conv.context_data = {}
        conv.save(update_fields=["state", "step", "context_data", "updated_at"])

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.PROCESS_CANCELLED,
            reply_markup=SellerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "cancelled"}

    def start_new_listing(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Initiates the product listing creation wizard."""
        conv = self._get_conversation_state(user)
        conv.state = "CREATING_LISTING"
        conv.step = SellerWorkflowStep.BREED
        conv.context_data = {"pictures": []}
        conv.save(update_fields=["state", "step", "context_data", "updated_at"])

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.LISTING_WIZARD_WELCOME,
        )
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.CATEGORY_TYPE_PROMPT,
            reply_markup=SellerKeyboards.breed_options(),
        )
        return {"handled": True, "action": "started_listing_wizard"}

    def handle_callback(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
        data: str,
    ) -> Dict[str, Any]:
        """Routes callback queries triggered by inline buttons in seller bot."""
        conv = self._get_conversation_state(user)
        ctx = conv.context_data or {}

        # 1. Category Type (Breed) Selected
        if data in BREED_OPTIONS:
            ctx["breed"] = data
            conv.step = SellerWorkflowStep.TITLE
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"Selected: {data}",
            )
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.PRODUCT_TITLE_PROMPT,
            )
            return {"handled": True, "action": "selected_breed"}

        # 2. Sales Option Selected (Auction / Buy It Now)
        elif data in ["Auction", "Buy It Now"]:
            ctx["category"] = data
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"Selected: {data}",
            )

            if data == "Auction":
                conv.step = SellerWorkflowStep.AUCTION_STARTINGPRICE
                conv.context_data = ctx
                conv.save(update_fields=["step", "context_data", "updated_at"])

                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.STARTING_PRICE_PROMPT,
                )
            else:
                conv.step = SellerWorkflowStep.BUYNOW_PRICE
                conv.context_data = ctx
                conv.save(update_fields=["step", "context_data", "updated_at"])

                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=SellerMessages.BUYNOW_PRICE_PROMPT,
                )
            return {"handled": True, "action": "selected_sales_type"}

        # 3. Auction End Time Presets
        elif data in ["1_day_auction", "2_days_auction", "3_days_auction", "5_days_auction", "10_days_auction"]:
            days = data.split("_")[0]
            ctx["auction_days"] = days
            conv.step = SellerWorkflowStep.PICTURE
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=f"Selected: {days} day(s)",
            )
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_IMAGES_PROMPT,
            )
            return {"handled": True, "action": "selected_end_time_preset"}

        elif data == "manual_input_auction":
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.END_TIME_MANUAL_PROMPT,
            )
            return {"handled": True, "action": "manual_end_time"}

        # 4. Skip Picture Callback
        elif data == "skip_picture":
            conv.step = SellerWorkflowStep.VIDEO
            conv.save(update_fields=["step", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_VIDEO_PROMPT,
                reply_markup=SellerKeyboards.skip_video(),
            )
            return {"handled": True, "action": "skipped_picture"}

        # 5. Skip Video Callback
        elif data == "skip_video":
            return self._finalize_listing(user, chat_id)

        # 6. Delete listing
        elif data.startswith("delete_"):
            listing_id_str = data.split("_")[-1]
            try:
                lid = int(listing_id_str)
                Listing.objects.filter(tenant=self.tenant, id=lid, seller_id=str(user.telegram_user_id)).update(status=ListingStatus.CLOSED)
                self.telegram_service.answer_callback_query(callback_id, f"Listing #{lid} deleted.")
            except Exception:
                pass
            return {"handled": True, "action": "deleted_listing"}

        # 7. Add seller money
        elif data.startswith("add_seller_money_"):
            conv.step = SellerWorkflowStep.WALLET_AMOUNT
            conv.save(update_fields=["step", "updated_at"])
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.ADD_MONEY_PROMPT,
                reply_markup=SellerKeyboards.wallet_amount_options(),
            )
            return {"handled": True, "action": "wallet_amounts"}

        elif data.startswith("wallet_amount_save_"):
            amt = data.split("_")[-1]
            ctx["amount_to_add"] = amt
            conv.step = SellerWorkflowStep.WALLET_AMOUNT
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            bank_details = getattr(self.tenant, "metadata", {}).get("bank_details") or "Maybank 512345678901 CYG Aquatics"
            msg = SellerMessages.PAYMENT_INSTRUCTIONS.format(amount=amt, bank_details=bank_details)
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=SellerKeyboards.enter_payment_details(user.telegram_user_id),
            )
            return {"handled": True, "action": "wallet_payment_instructions"}

        elif data.startswith("give_payment_details_"):
            conv.step = SellerWorkflowStep.GET_PAYMENT_DETAILS
            conv.save(update_fields=["step", "updated_at"])
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.ATTACH_PAYMENT_PROOF,
            )
            return {"handled": True, "action": "prompt_payment_proof"}

        elif data == "submit_yes_payment":
            amount_to_add = ctx.get("amount_to_add", "10")
            photo_path = ctx.get("payment_proof_image", "")
            from apps.finance.services import deposit
            try:
                deposit(
                    tenant=self.tenant,
                    user_id=str(user.telegram_user_id),
                    amount=Decimal(amount_to_add),
                    currency=getattr(self.tenant, "currency", "MYR") or "MYR",
                    description=f"Seller wallet deposit via Telegram proof {photo_path}",
                    metadata={"payment_proof": photo_path, "username": user.username or ""}
                )
            except Exception as e:
                logger.error("Failed to deposit funds for user %s: %s", user.telegram_user_id, e)

            conv.step = SellerWorkflowStep.IDLE
            conv.context_data = {}
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.PAYMENT_SUBMITTED_SUCCESS,
            )
            return {"handled": True, "action": "submitted_payment"}

        elif data == "cancel_no_payment":
            conv.step = SellerWorkflowStep.GET_PAYMENT_DETAILS
            conv.save(update_fields=["step", "updated_at"])
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.ATTACH_PAYMENT_PROOF,
                reply_markup=SellerKeyboards.enter_payment_details(user.telegram_user_id),
            )
            return {"handled": True, "action": "cancelled_payment"}

        # 8. Start trigger
        elif data == "trigger_start":
            return self.handle_start(user, chat_id)

        # 9. Start new listing from button
        elif data == "start_new_listing":
            return self.start_new_listing(user, chat_id)

        return {"handled": False, "action": "unrecognized_seller_callback"}

    def handle_photo(
        self,
        user: TelegramUser,
        chat_id: int,
        photo_sizes: List[Dict[str, Any]],
        caption: str = "",
    ) -> Dict[str, Any]:
        """Handles picture upload in listing wizard and wallet payment flow."""
        conv = self._get_conversation_state(user)

        # 1. Handle Payment Proof in GET_PAYMENT_DETAILS step
        if conv.step == SellerWorkflowStep.GET_PAYMENT_DETAILS:
            largest_photo = max(photo_sizes, key=lambda p: p.get("file_size", 0))
            file_id = largest_photo.get("file_id")
            media_root = getattr(settings, "MEDIA_ROOT", "media")
            file_rel_path = ""
            if file_id:
                try:
                    if hasattr(self.telegram_service, "get_file"):
                        file_info = self.telegram_service.get_file(file_id)
                        if file_info and file_info.get("ok"):
                            tg_file_path = file_info.get("result", {}).get("file_path")
                            if tg_file_path:
                                ext = os.path.splitext(tg_file_path)[1] or ".jpg"
                                dest_filename = f"{uuid.uuid4().hex}{ext}"
                                dest_dir = os.path.join(media_root, "photos", "payment_proofs")
                                os.makedirs(dest_dir, exist_ok=True)
                                dest_path = os.path.join(dest_dir, dest_filename)
                                self.telegram_service.download_file(tg_file_path, dest_path)
                                file_rel_path = f"photos/payment_proofs/{dest_filename}"
                except Exception as exc:
                    logger.warning("Could not download payment proof image %s: %s", file_id, exc)

            ctx = conv.context_data or {}
            ctx["payment_proof_image"] = file_rel_path or file_id
            conv.step = SellerWorkflowStep.CONFIRM_PAYMENT
            conv.context_data = ctx
            conv.save(update_fields=["step", "context_data", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.CONFIRM_PAYMENT_PROMPT,
                reply_markup=SellerKeyboards.confirm_payment(),
            )
            return {"handled": True, "action": "received_payment_proof", "payment_proof": file_rel_path or file_id}

        if conv.step != SellerWorkflowStep.PICTURE:
            return {"handled": False, "reason": "not_in_picture_step"}

        ctx = conv.context_data or {}
        pictures = ctx.get("pictures", [])

        # Get best resolution file_id
        largest_photo = max(photo_sizes, key=lambda p: p.get("file_size", 0))
        file_id = largest_photo.get("file_id")

        if file_id:
            pictures.append(file_id)

        ctx["pictures"] = pictures
        conv.context_data = ctx
        conv.save(update_fields=["context_data", "updated_at"])

        if len(pictures) >= 4:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.MAX_PICTURES_REACHED,
            )
            conv.step = SellerWorkflowStep.VIDEO
            conv.save(update_fields=["step", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_VIDEO_PROMPT,
                reply_markup=SellerKeyboards.skip_video(),
            )
        else:
            remaining = 4 - len(pictures)
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.UPLOAD_MORE_PICTURES.format(remaining=remaining),
                reply_markup=SellerKeyboards.skip_picture(),
            )

        return {"handled": True, "action": "uploaded_picture", "count": len(pictures), "photo_count": len(pictures)}

    def get_seller_profile(self, user: TelegramUser) -> Optional[Seller]:
        """Fetch seller profile for given TelegramUser within this tenant."""
        return Seller.objects.filter(tenant=self.tenant, telegram_user=user).first()

    def handle_video(
        self,
        user: TelegramUser,
        chat_id: int,
        video_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handles video upload in listing wizard."""
        conv = self._get_conversation_state(user)
        if conv.step != SellerWorkflowStep.VIDEO:
            return {"handled": False, "reason": "not_in_video_step"}

        file_size = video_data.get("file_size", 0)
        if file_size > 10 * 1024 * 1024:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.VIDEO_TOO_LARGE,
            )
            return {"handled": True, "action": "video_too_large"}

        file_id = video_data.get("file_id")
        ctx = conv.context_data or {}
        if file_id:
            ctx["video"] = file_id
            conv.context_data = ctx
            conv.save(update_fields=["context_data", "updated_at"])

        return self._finalize_listing(user, chat_id)

    def _finalize_listing(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Creates the listing in database and sends exact legacy success message."""
        conv = self._get_conversation_state(user)
        ctx = conv.context_data or {}

        pictures = ctx.get("pictures", [])
        if not pictures:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.AT_LEAST_ONE_PICTURE,
            )
            return {"handled": True, "action": "require_picture"}

        # Resolve or create Seller record
        seller, _ = Seller.objects.get_or_create(
            tenant=self.tenant,
            telegram_user=user,
            defaults={
                "seller_id": str(user.telegram_user_id),
                "business_name": user.first_name or f"Seller{user.telegram_user_id}",
                "contact_name": ctx.get("contact", "") or user.first_name,
                "phone": "N/A",
                "status": SellerStatus.ACTIVE,
            },
        )

        title = ctx.get("title") or "Fish Listing"
        description = ctx.get("description") or ""
        breed = ctx.get("breed") or "General"
        quantity = int(ctx.get("quantity") or 1)
        sales_type = ctx.get("category") or "Auction"

        listing_type = ListingType.AUCTION if sales_type == "Auction" else ListingType.BUY_NOW

        # Create Listing
        listing = create_listing(
            tenant=self.tenant,
            seller_id=str(user.telegram_user_id),
            seller_username=user.username or "",
            title=title,
            description=description,
            category=breed,
            listing_type=listing_type,
            quantity=quantity,
            metadata={
                "contact": ctx.get("contact"),
                "starting_price": str(ctx.get("starting_price") or 10),
                "auto_accept_price": str(ctx.get("auto_accept_price") or ""),
                "min_bid": str(ctx.get("min_bid") or 5),
                "buynow_price": str(ctx.get("buynow_price") or 0) if sales_type != "Auction" else "",
                "start_date": str(ctx.get("start_date") or ""),
                "start_time": str(ctx.get("start_time") or ""),
                "auction_days": str(ctx.get("auction_days") or ""),
            },
        )

        # Attach images and download to media if possible
        media_root = getattr(settings, "MEDIA_ROOT", "media")
        for idx, file_id in enumerate(pictures):
            file_rel_path = ""
            try:
                if hasattr(self.telegram_service, "get_file"):
                    file_info = self.telegram_service.get_file(file_id)
                    if file_info and file_info.get("ok"):
                        tg_file_path = file_info.get("result", {}).get("file_path")
                        if tg_file_path:
                            ext = os.path.splitext(tg_file_path)[1] or ".jpg"
                            dest_filename = f"{uuid.uuid4().hex}{ext}"
                            dest_dir = os.path.join(media_root, "listings", str(self.tenant.id))
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_path = os.path.join(dest_dir, dest_filename)
                            self.telegram_service.download_file(tg_file_path, dest_path)
                            file_rel_path = f"listings/{self.tenant.id}/{dest_filename}"
            except Exception as exc:
                logger.warning("Could not download telegram image %s: %s", file_id, exc)

            attach_listing_image(
                listing=listing,
                image=file_rel_path if file_rel_path else None,
                file_url=file_rel_path,
                telegram_file_id=file_id,
                order=idx,
            )

        # Process and download video if uploaded
        video_file_id = ctx.get("video")
        if video_file_id:
            video_rel_path = ""
            try:
                if hasattr(self.telegram_service, "get_file"):
                    file_info = self.telegram_service.get_file(video_file_id)
                    if file_info and file_info.get("ok"):
                        tg_file_path = file_info.get("result", {}).get("file_path")
                        if tg_file_path:
                            ext = os.path.splitext(tg_file_path)[1] or ".mp4"
                            dest_filename = f"{uuid.uuid4().hex}{ext}"
                            dest_dir = os.path.join(media_root, "videos", str(self.tenant.id))
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_path = os.path.join(dest_dir, dest_filename)
                            self.telegram_service.download_file(tg_file_path, dest_path)
                            video_rel_path = f"videos/{self.tenant.id}/{dest_filename}"
            except Exception as exc:
                logger.warning("Could not download telegram video %s: %s", video_file_id, exc)

            listing.metadata["video"] = video_rel_path
            listing.metadata["video_file_id"] = video_file_id
            listing.save(update_fields=["metadata", "updated_at"])

        # Reset state
        conv.state = "IDLE"
        conv.step = SellerWorkflowStep.IDLE
        conv.context_data = {}
        conv.save(update_fields=["state", "step", "context_data", "updated_at"])

        # Send exact legacy success messages
        base_url = getattr(settings, "BASE_SITE_URL", "https://auctionbot.shop")
        edit_url = f"{base_url}/fish/{listing.id}/{user.telegram_user_id}/edit/"

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.LISTING_SAVED_SUCCESS,
        )
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=SellerMessages.EDIT_DETAILS_PROMPT,
            reply_markup=SellerKeyboards.edit_details(edit_url),
        )

        return {"handled": True, "action": "listing_created", "listing_id": listing.id}

    def _handle_my_listings(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Renders seller's own listings with delete and start buttons."""
        listings = Listing.objects.filter(
            tenant=self.tenant,
            seller_id=str(user.telegram_user_id),
        ).exclude(status=ListingStatus.CLOSED).order_by("-created_at")

        if not listings.exists():
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=SellerMessages.NO_LISTINGS_AVAILABLE,
            )
            return {"handled": True, "count": 0}

        for item in listings:
            msg = (
                f"Listing ID: [#{item.id}]\n"
                f"Title: {item.title}\n"
                f"Category Type: {item.category}\n"
                f"Sales Type: {item.listing_type}\n"
                f"Contact Details: {item.seller_username or 'Seller'}\n"
                f"Status: {item.status}\n"
                f"{'-' * 20}\n"
            )
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=SellerKeyboards.my_listing_actions(item.id),
            )

        return {"handled": True, "count": listings.count()}
