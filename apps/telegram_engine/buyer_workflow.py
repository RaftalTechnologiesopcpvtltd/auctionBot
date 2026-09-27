"""Buyer Telegram Bot workflow replicating exact legacy UX from show_bidding_list.py.

Underneath the identical legacy Telegram UX:
- Multi-tenant tenant scoping and server-side authorization.
- Phase 05 authoritative concurrent bidding engine (`services/bids.py`).
- Phase 06 financial ledger for wallet and won checkout (`apps/finance/`).
- Persistent recoverable conversation states via TelegramConversationState.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.utils import timezone
from django.utils.timezone import localtime

from apps.bidding.models import Auction, AuctionStatus, Bid, BuyerWishlist
from apps.listings.models import Listing, ListingImage, ListingStatus, ListingType
from apps.telegram_engine.keyboards import BuyerKeyboards
from apps.telegram_engine.messages import BuyerMessages
from apps.telegram_engine.models import TelegramBotConfig, TelegramConversationState, TelegramUser
from apps.tenants.models import Tenant
from services.bids import BiddingError, place_bid
from services.telegram import TelegramService

logger = logging.getLogger(__name__)


class BuyerWorkflowState:
    IDLE = "IDLE"
    WAITING_PASSWORD = "WAITING_PASSWORD"
    WAITING_CONTACT_TEXT = "WAITING_CONTACT_TEXT"


class BuyerWorkflow:
    """Manages buyer interactions, auction browsing, bidding, and account preferences."""

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
        """Fetch or create persistent conversation state for this buyer within this tenant."""
        state, _ = TelegramConversationState.objects.get_or_create(
            tenant=self.tenant,
            telegram_user=user,
            bot_type=self.bot_config.bot_type,
            defaults={"state": BuyerWorkflowState.IDLE, "step": "", "context_data": {}},
        )
        return state

    def handle_start(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Exact legacy /start behavior."""
        # 1. Immediate greeting (clearing existing keyboard)
        try:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.GREETING,
                reply_markup={"remove_keyboard": True},
            )
        except Exception as exc:
            logger.warning("Could not send buyer greeting: %s", exc)

        # 2. Check if user is blocked
        if user.is_blocked:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.BLOCKED_USER,
            )
            return {"handled": True, "action": "blocked"}

        # 3. Check if contact details are required and missing
        if self.bot_config.require_contact_details and not user.contact_details:
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.CONTACT_REQUIRED,
                reply_markup=BuyerKeyboards.update_buyer_contact_details_button(),
            )
            return {"handled": True, "action": "prompt_contact"}

        # 4. Check if password authentication is required
        if self.bot_config.require_password and not user.is_authorized:
            conv = self._get_conversation_state(user)
            conv.state = BuyerWorkflowState.WAITING_PASSWORD
            conv.save(update_fields=["state", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.PASSWORD_PROMPT,
            )
            return {"handled": True, "action": "prompt_password"}

        # 5. Already authorized or no password required
        conv = self._get_conversation_state(user)
        conv.state = BuyerWorkflowState.IDLE
        conv.save(update_fields=["state", "updated_at"])

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=BuyerMessages.CHOOSE_OPTION,
            reply_markup=BuyerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "main_menu"}

    def handle_text(self, user: TelegramUser, chat_id: int, text: str) -> Dict[str, Any]:
        """Routes text messages and custom reply keyboard choices."""
        text_clean = text.strip()

        # Handle cancel command
        if text_clean == "/cancel":
            return self.handle_cancel(user, chat_id)

        conv = self._get_conversation_state(user)

        # 1. State: WAITING_PASSWORD
        if conv.state == BuyerWorkflowState.WAITING_PASSWORD:
            if text_clean == self.bot_config.access_password:
                user.is_authorized = True
                user.save(update_fields=["is_authorized"])
                conv.state = BuyerWorkflowState.IDLE
                conv.save(update_fields=["state", "updated_at"])

                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=BuyerMessages.PASSWORD_AUTHORIZED,
                    reply_markup=BuyerKeyboards.main_menu(),
                )
                return {"handled": True, "action": "password_success"}
            else:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=BuyerMessages.PASSWORD_INCORRECT,
                )
                return {"handled": True, "action": "password_failed"}

        # 2. State: WAITING_CONTACT_TEXT
        if conv.state == BuyerWorkflowState.WAITING_CONTACT_TEXT:
            if not text_clean:
                self.telegram_service.send_message(
                    chat_id=chat_id,
                    text=BuyerMessages.CONTACT_SAVE_ERROR,
                )
                return {"handled": True, "action": "contact_save_error"}

            user.contact_details = text_clean
            user.save(update_fields=["contact_details"])
            conv.state = BuyerWorkflowState.IDLE
            conv.save(update_fields=["state", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.CONTACT_UPDATED,
                reply_markup=BuyerKeyboards.main_menu(),
            )
            return {"handled": True, "action": "contact_saved"}

        # 3. Custom Keyboard Button Handlers
        if text_clean == "Start":
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.SHOW_LISTINGS_PROMPT,
                reply_markup=BuyerKeyboards.show_listings_prompt(),
            )
            return {"handled": True, "action": "start_listings_prompt"}

        elif text_clean == "❤️ My Favourites":
            return self._handle_my_favourites(user, chat_id)

        elif text_clean == "Check Out":
            return self._handle_check_out(user, chat_id)

        elif text_clean == "My Bids":
            return self._handle_my_bids(user, chat_id)

        elif text_clean == "Auction Ending Soon":
            return self._handle_auction_ending_soon(user, chat_id)

        elif text_clean == "Join Group":
            group_details = (
                getattr(self.tenant, "metadata", {}).get("group_details")
                or f"Join the official {self.tenant.name} Telegram group community!"
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(group_details))
            return {"handled": True, "action": "join_group"}

        elif text_clean == "About":
            about_us = (
                getattr(self.tenant, "metadata", {}).get("about_us")
                or f"About {self.tenant.name}: Dedicated online livestock & aquatics auction platform."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(about_us))
            return {"handled": True, "action": "about"}

        elif text_clean == "Helpdesk":
            helpdesk_details = (
                getattr(self.tenant, "metadata", {}).get("helpdesk_details")
                or f"Contact {self.tenant.name} support for inquiries and dispute resolution."
            )
            self.telegram_service.send_message(chat_id=chat_id, text=str(helpdesk_details))
            return {"handled": True, "action": "helpdesk"}

        elif text_clean == "My Delivery Address":
            details_str = user.contact_details or "None"
            msg = f"Your contact details are: \n{details_str}"
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=BuyerKeyboards.update_buyer_contact_details_button(),
            )
            return {"handled": True, "action": "my_delivery_address"}

        # Unrecognized message fallback
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=BuyerMessages.CHOOSE_OPTION,
            reply_markup=BuyerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "default_menu"}

    def handle_cancel(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Exact legacy /cancel behavior."""
        conv = self._get_conversation_state(user)
        conv.state = BuyerWorkflowState.IDLE
        conv.step = ""
        conv.context_data = {}
        conv.save(update_fields=["state", "step", "context_data", "updated_at"])

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=BuyerMessages.PROCESS_CANCELLED,
            reply_markup=BuyerKeyboards.main_menu(),
        )
        return {"handled": True, "action": "cancelled"}

    def handle_callback(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
        data: str,
    ) -> Dict[str, Any]:
        """Routes callback queries triggered by inline buttons."""
        if user.is_blocked:
            self.telegram_service.answer_callback_query(
                callback_query_id=callback_id,
                text=BuyerMessages.BLOCKED_USER,
            )
            return {"handled": True, "action": "blocked"}

        # 1. Show All Auctions
        if data == "show_listings":
            return self._handle_show_listings(user, chat_id, callback_id)

        # 2. Show All Buy It Now
        elif data == "buy_now_listings":
            return self._handle_buy_now_listings(user, chat_id, callback_id)

        # 3. Start Bid on a listing
        elif data.startswith("start_bid_"):
            listing_id_str = data.split("_")[2]
            return self._handle_start_bid(user, chat_id, callback_id, listing_id_str)

        # 4. Bid Amount Selected
        elif data.startswith("bid_amount_"):
            return self._handle_bid_amount(user, chat_id, callback_id, data)

        # 5. Refresh Auction Listing
        elif data.startswith("refresh_auction_listing_"):
            listing_id_str = data.split("_")[-1]
            return self._handle_refresh_listing(user, chat_id, callback_id, listing_id_str)

        # 6. Add to Wishlist (❤️)
        elif data.startswith("wishlist_"):
            listing_id_str = data.split("_")[1]
            return self._handle_add_wishlist(user, callback_id, listing_id_str)

        # 7. Remove from Wishlist (Remove ❤️)
        elif data.startswith("remove_from_wishlist_"):
            wishlist_id_str = data.split("_")[-1]
            return self._handle_remove_wishlist(user, callback_id, wishlist_id_str)

        # 8. Update Delivery Contact Details
        elif data.startswith("update_buyer_contact_details_"):
            conv = self._get_conversation_state(user)
            conv.state = BuyerWorkflowState.WAITING_CONTACT_TEXT
            conv.save(update_fields=["state", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.ENTER_CONTACT_DETAILS,
            )
            return {"handled": True, "action": "waiting_contact_text"}

        # 9. Cancel process inline
        elif data == "cancel_process":
            conv = self._get_conversation_state(user)
            conv.state = BuyerWorkflowState.IDLE
            conv.save(update_fields=["state", "updated_at"])

            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.PROCESS_CANCELLED_INLINE,
            )
            return {"handled": True, "action": "cancel_process"}

        return {"handled": False, "action": "unrecognized_callback"}

    # -------------------------------------------------------------------------
    # Internal Helpers & Display Logic
    # -------------------------------------------------------------------------

    def _render_auction_card(
        self,
        chat_id: int,
        listing: Listing,
        auction: Optional[Auction] = None,
        wishlist_id: Optional[int] = None,
    ) -> None:
        """Renders an auction listing matching exact legacy structure and styling."""
        if not auction and hasattr(listing, "auction"):
            auction = listing.auction

        current_bid_price = int(auction.current_price) if auction and auction.current_price > 0 else int(auction.starting_price if auction else 0)
        end_time_str = localtime(auction.end_at).strftime("%d-%m-%Y %I:%M %p") if auction and auction.end_at else "N/A"
        contact_str = getattr(listing, "seller_username", "") or "Authorized Seller"

        # Determine last bidder display
        has_bids = False
        last_bidder_str = "None"
        if auction and auction.highest_bid:
            has_bids = True
            last_bidder_str = auction.highest_bid.bidder_name or "Bidder"

        caption_lines = [
            f"Listing ID: [#{listing.id}]",
            f"Title: {listing.title}",
            f"Quantity: {listing.quantity}",
            f"Contact Details: {contact_str}",
            "-" * 20,
            f"Auction Price: ${current_bid_price}",
            f"Auction End Time: {end_time_str}",
            "-" * 20,
            f"Current Bidder: {last_bidder_str}",
        ]
        caption_text = "\n".join(caption_lines)

        # 1. Send photos if available, else send text caption
        images = list(listing.images.filter(tenant=self.tenant).order_by("order"))
        if images and images[0].telegram_file_id:
            try:
                self.telegram_service.send_photo(
                    chat_id=chat_id,
                    photo=images[0].telegram_file_id,
                    caption=caption_text,
                )
            except Exception as exc:
                logger.warning("Could not send photo for listing #%s: %s", listing.id, exc)
                self.telegram_service.send_message(chat_id=chat_id, text=caption_text)
        else:
            self.telegram_service.send_message(chat_id=chat_id, text=caption_text)

        # 2. Description
        desc_text = f"Description: {listing.description or 'No description provided.'}"
        self.telegram_service.send_message(chat_id=chat_id, text=desc_text)

        # 3. Action buttons (attached to video status)
        if wishlist_id:
            buttons = BuyerKeyboards.create_wishlist_bid_button(listing.id, wishlist_id, has_bids=has_bids)
        else:
            buttons = BuyerKeyboards.create_bid_button(listing.id, has_bids=has_bids)

        self.telegram_service.send_message(
            chat_id=chat_id,
            text=BuyerMessages.NO_VIDEO_AVAILABLE,
            reply_markup=buttons,
        )

        # 4. Divider
        self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.DIVIDER)

    def _handle_show_listings(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
    ) -> Dict[str, Any]:
        """Displays all open auctions for this tenant."""
        from services.listings import ensure_active_auction_for_listing

        # Auto-ensure any approved AUCTION listings have active Auction models
        approved_listings = Listing.objects.filter(
            tenant=self.tenant,
            status=ListingStatus.APPROVED,
            listing_type=ListingType.AUCTION,
        )
        for item in approved_listings:
            ensure_active_auction_for_listing(item)

        auctions = (
            Auction.objects.filter(
                tenant=self.tenant,
                status=AuctionStatus.ACTIVE,
                listing__status=ListingStatus.APPROVED,
            )
            .select_related("listing", "highest_bid")
            .order_by("-created_at")
        )

        if not auctions.exists():
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.NO_LISTINGS_MOMENT,
            )
            return {"handled": True, "count": 0}

        for auction in auctions:
            self._render_auction_card(chat_id, auction.listing, auction=auction)

        return {"handled": True, "count": auctions.count()}

    def _handle_buy_now_listings(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
    ) -> Dict[str, Any]:
        """Displays Buy It Now catalog listings."""
        listings = Listing.objects.filter(
            tenant=self.tenant,
            status=ListingStatus.APPROVED,
            listing_type=ListingType.BUY_NOW,
        ).order_by("-created_at")

        if not listings.exists():
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.NO_LISTINGS_MOMENT,
            )
            return {"handled": True, "count": 0}

        for listing in listings:
            self._render_auction_card(chat_id, listing)

        return {"handled": True, "count": listings.count()}

    def _handle_start_bid(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
        listing_id_str: str,
    ) -> Dict[str, Any]:
        """Prompt buyer with bid increment buttons."""
        try:
            listing_id = int(listing_id_str)
            listing = Listing.objects.get(tenant=self.tenant, id=listing_id)
            auction = Auction.objects.select_related("highest_bid").get(tenant=self.tenant, listing=listing)
        except (ValueError, Listing.DoesNotExist, Auction.DoesNotExist):
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.LISTING_NOT_FOUND)
            return {"handled": True, "action": "not_found"}

        if auction.status != AuctionStatus.ACTIVE:
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.LISTING_CLOSED)
            return {"handled": True, "action": "closed"}

        # Acknowledge start of process
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=BuyerMessages.STARTING_BID_PROCESS.format(listing_id=listing.id),
        )

        current_bid_price = int(auction.current_price) if auction.current_price > 0 else int(auction.starting_price)
        if auction.highest_bid:
            current_bidder = auction.highest_bid.bidder_name or "Bidder"
            prompt_text = BuyerMessages.CHOOSE_BID_AMOUNT_WITH_BIDDER.format(
                current_bid_price=current_bid_price,
                current_bidder=current_bidder,
            )
        else:
            prompt_text = BuyerMessages.CHOOSE_BID_AMOUNT_NO_BIDDER.format(
                current_bid_price=current_bid_price
            )

        min_bid = int(auction.bid_increment) if auction.bid_increment > 0 else 5
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=prompt_text,
            reply_markup=BuyerKeyboards.create_bid_amount_buttons(listing.id, min_bid=min_bid),
        )
        return {"handled": True, "action": "prompt_bid_amount"}

    def _handle_bid_amount(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
        data: str,
    ) -> Dict[str, Any]:
        """Executes atomic bid submission through Phase 05 concurrent bidding engine."""
        parts = data.split("_")
        if len(parts) != 4:
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.INVALID_BID_DATA)
            return {"handled": True, "action": "invalid_data"}

        try:
            listing_id = int(parts[2])
            increment_amount = Decimal(parts[3])
            listing = Listing.objects.get(tenant=self.tenant, id=listing_id)
            auction = Auction.objects.get(tenant=self.tenant, listing=listing)
        except (ValueError, Listing.DoesNotExist, Auction.DoesNotExist):
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.LISTING_NOT_FOUND)
            return {"handled": True, "action": "not_found"}

        # Legacy behavior: increment is added to current price
        base_price = auction.current_price if auction.current_price > 0 else auction.starting_price
        target_bid_amount = base_price + increment_amount

        bidder_name = user.username or user.first_name or f"Bidder{user.telegram_user_id}"

        try:
            bid, updated_auction = place_bid(
                auction_or_id=auction,
                bidder_id=str(user.telegram_user_id),
                amount=target_bid_amount,
                bidder_name=bidder_name,
                tenant=self.tenant,
            )
        except BiddingError as err:
            logger.warning("Bid error on Auction #%s by user %s: %s", auction.id, user.telegram_user_id, err)
            self.telegram_service.send_message(chat_id=chat_id, text=f"⚠️ Bidding error: {err}")
            return {"handled": True, "action": "bid_error", "error": str(err)}
        except Exception as exc:
            logger.exception("Unexpected error placing bid: %s", exc)
            self.telegram_service.send_message(chat_id=chat_id, text="Error occurred while placing bid. Please try again.")
            return {"handled": True, "action": "unexpected_error"}

        # Exact legacy success message
        success_text = BuyerMessages.BID_PLACED_SUCCESS.format(
            amount=int(increment_amount),
            listing_id=listing.id,
            new_amount=int(updated_auction.current_price),
        )
        self.telegram_service.send_message(
            chat_id=chat_id,
            text=success_text,
            reply_markup=BuyerKeyboards.bid_placed_success_buttons(listing.id),
        )
        return {"handled": True, "action": "bid_placed", "bid_id": bid.id}

    def _handle_refresh_listing(
        self,
        user: TelegramUser,
        chat_id: int,
        callback_id: str,
        listing_id_str: str,
    ) -> Dict[str, Any]:
        """Refreshes a specific auction listing card."""
        try:
            listing_id = int(listing_id_str)
            listing = Listing.objects.get(tenant=self.tenant, id=listing_id)
            auction = Auction.objects.select_related("highest_bid").get(tenant=self.tenant, listing=listing)
        except (ValueError, Listing.DoesNotExist, Auction.DoesNotExist):
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.LISTING_CLOSED)
            return {"handled": True, "action": "closed"}

        if auction.status != AuctionStatus.ACTIVE:
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.LISTING_CLOSED)
            return {"handled": True, "action": "closed"}

        self._render_auction_card(chat_id, listing, auction=auction)
        return {"handled": True, "action": "refreshed"}

    def _handle_add_wishlist(
        self,
        user: TelegramUser,
        callback_id: str,
        listing_id_str: str,
    ) -> Dict[str, Any]:
        """Adds listing to user's favorites (❤️)."""
        try:
            listing_id = int(listing_id_str)
            listing = Listing.objects.get(tenant=self.tenant, id=listing_id)
        except (ValueError, Listing.DoesNotExist):
            self.telegram_service.answer_callback_query(callback_id, BuyerMessages.LISTING_NOT_FOUND)
            return {"handled": True, "action": "not_found"}

        _, created = BuyerWishlist.objects.get_or_create(
            tenant=self.tenant,
            telegram_user=user,
            listing=listing,
        )

        if created:
            answer = BuyerMessages.WISHLIST_ADDED.format(listing_id=listing.id)
        else:
            answer = BuyerMessages.WISHLIST_ALREADY

        self.telegram_service.answer_callback_query(callback_id, answer)
        return {"handled": True, "action": "wishlist_added", "created": created}

    def _handle_remove_wishlist(
        self,
        user: TelegramUser,
        callback_id: str,
        wishlist_id_str: str,
    ) -> Dict[str, Any]:
        """Removes an item from user's favorites."""
        try:
            wishlist_id = int(wishlist_id_str)
            deleted, _ = BuyerWishlist.objects.filter(
                tenant=self.tenant,
                id=wishlist_id,
                telegram_user=user,
            ).delete()
        except ValueError:
            deleted = False

        if deleted:
            self.telegram_service.answer_callback_query(callback_id, BuyerMessages.WISHLIST_REMOVED)
        else:
            self.telegram_service.answer_callback_query(callback_id, BuyerMessages.WISHLIST_NOT_FOUND)

        return {"handled": True, "action": "wishlist_removed"}

    def _handle_my_favourites(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Renders wishlisted items."""
        items = (
            BuyerWishlist.objects.filter(
                tenant=self.tenant,
                telegram_user=user,
                listing__status=ListingStatus.APPROVED,
            )
            .select_related("listing", "listing__auction")
            .order_by("-created_at")
        )

        if not items.exists():
            self.telegram_service.send_message(
                chat_id=chat_id,
                text=BuyerMessages.WISHLIST_EMPTY,
                reply_markup=BuyerKeyboards.main_menu(),
            )
            return {"handled": True, "count": 0}

        for item in items:
            self._render_auction_card(chat_id, item.listing, wishlist_id=item.id)

        return {"handled": True, "count": items.count()}

    def _handle_check_out(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Displays won auctions for checkout."""
        won_auctions = (
            Auction.objects.filter(
                tenant=self.tenant,
                winner_id=str(user.telegram_user_id),
                status__in=[AuctionStatus.CLOSED, AuctionStatus.SOLD],
            )
            .select_related("listing")
            .order_by("-updated_at")
        )

        if not won_auctions.exists():
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.NO_LISTINGS_FOUND)
            return {"handled": True, "count": 0}

        self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.CHECKOUT_HEADER)

        for auction in won_auctions:
            listing = auction.listing
            start_str = localtime(auction.start_at).strftime("%d-%m-%Y %I:%M %p") if auction.start_at else "N/A"
            end_str = localtime(auction.end_at).strftime("%d-%m-%Y %I:%M %p") if auction.end_at else "N/A"
            sold_date = localtime(auction.updated_at).strftime("%d-%m-%Y")
            price = int(auction.winning_price or auction.current_price)

            msg = (
                f"Listing ID: [#{listing.id}]\n"
                f"Title: {listing.title}\n"
                f"Contact Details: {listing.seller_username or 'Authorized Seller'}\n"
                f"{'-' * 20}\n"
                f"Price: ${price}\n"
                f"Quantity: {listing.quantity}\n"
                f"Start Time: {start_str}\n"
                f"End Time: {end_str}\n"
                f"Purchase Date: {sold_date}\n"
            )
            self.telegram_service.send_message(chat_id=chat_id, text=msg)

        return {"handled": True, "count": won_auctions.count()}

    def _handle_my_bids(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Displays user's active bids."""
        active_bids = (
            Bid.objects.filter(
                tenant=self.tenant,
                bidder_id=str(user.telegram_user_id),
                auction__status=AuctionStatus.ACTIVE,
            )
            .select_related("auction", "auction__listing")
            .order_by("-placed_at")
        )

        seen_listings = set()
        unique_auctions = []
        for bid in active_bids:
            if bid.auction_id not in seen_listings:
                seen_listings.add(bid.auction_id)
                unique_auctions.append(bid.auction)

        if not unique_auctions:
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.NO_LISTINGS_FOUND)
            return {"handled": True, "count": 0}

        self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.MY_BIDS_HEADER)

        for auction in unique_auctions:
            self._render_auction_card(chat_id, auction.listing, auction=auction)

        return {"handled": True, "count": len(unique_auctions)}

    def _handle_auction_ending_soon(self, user: TelegramUser, chat_id: int) -> Dict[str, Any]:
        """Displays auctions ending within 24 hours."""
        now = timezone.now()
        horizon = now + timedelta(days=1)

        bids = (
            Bid.objects.filter(
                tenant=self.tenant,
                bidder_id=str(user.telegram_user_id),
                auction__status=AuctionStatus.ACTIVE,
                auction__end_at__gte=now,
                auction__end_at__lte=horizon,
            )
            .select_related("auction", "auction__listing")
            .order_by("auction__end_at")
        )

        seen = set()
        ending_soon_auctions = []
        for b in bids:
            if b.auction_id not in seen:
                seen.add(b.auction_id)
                ending_soon_auctions.append(b.auction)

        if not ending_soon_auctions:
            self.telegram_service.send_message(chat_id=chat_id, text=BuyerMessages.NO_LISTINGS_FOUND)
            return {"handled": True, "count": 0}

        for auction in ending_soon_auctions:
            self._render_auction_card(chat_id, auction.listing, auction=auction)

        return {"handled": True, "count": len(ending_soon_auctions)}
