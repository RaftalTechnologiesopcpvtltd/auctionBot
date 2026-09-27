"""Centralized immutable keyboards reproducing exact legacy Telegram UX.

SOURCE OF TRUTH:
- E:\\auctionbots\\CYG_Aquatics_Malaysia\\show_bidding_list.py
- E:\\auctionbots\\CYG_Aquatics_Malaysia\\fish_registration.py
"""
from typing import Any, Dict, List, Optional


BREED_OPTIONS = [
    "Antiques, Art & Collectables",
    "Baby & Children",
    "Books, Music & Games",
    "Cars & Vehicles",
    "Clothing & Jewellery",
    "Electronic & Computer",
    "Home & Garden",
    "Pets & Accessories",
    "Sport & Fitness",
    "Miscellaneous Goods",
]

CATEGORY_OPTIONS = ["Auction", "Buy It Now"]
WALLET_AMOUNT_OPTIONS = ["10", "20", "50", "100"]


class BuyerKeyboards:
    """Exact keyboards from show_bidding_list.py."""

    @staticmethod
    def main_menu() -> Dict[str, Any]:
        """Exact legacy ReplyKeyboardMarkup (row_width=2)."""
        return {
            "keyboard": [
                [{"text": "Start"}, {"text": "Helpdesk"}],
                [{"text": "❤️ My Favourites"}, {"text": "Check Out"}],
                [{"text": "My Bids"}, {"text": "Auction Ending Soon"}],
                [{"text": "Join Group"}, {"text": "About"}],
                [{"text": "My Delivery Address"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }

    @staticmethod
    def show_listings_prompt() -> Dict[str, Any]:
        """Exact inline buttons shown on 'Start'."""
        return {
            "inline_keyboard": [
                [
                    {"text": "Show All Auctions", "callback_data": "show_listings"},
                    {"text": "Show All Buy It Now", "callback_data": "buy_now_listings"},
                ]
            ]
        }

    @staticmethod
    def create_bid_button(listing_id: int, has_bids: bool = False) -> Dict[str, Any]:
        """Exact inline buttons attached to each auction listing."""
        if has_bids:
            return {
                "inline_keyboard": [
                    [
                        {"text": "Start Bid", "callback_data": f"start_bid_{listing_id}"},
                        {"text": "❤️", "callback_data": f"wishlist_{listing_id}"},
                    ]
                ]
            }
        return {
            "inline_keyboard": [
                [
                    {"text": "Start Bid", "callback_data": f"start_bid_{listing_id}"},
                    {"text": "Make Offer ", "callback_data": f"make_auction_offer_{listing_id}"},
                    {"text": "❤️", "callback_data": f"wishlist_{listing_id}"},
                ]
            ]
        }

    @staticmethod
    def create_wishlist_bid_button(listing_id: int, wishlist_id: int, has_bids: bool = False) -> Dict[str, Any]:
        """Exact inline buttons attached to wishlisted items."""
        if has_bids:
            return {
                "inline_keyboard": [
                    [
                        {"text": "Start Bid", "callback_data": f"start_bid_{listing_id}"},
                        {"text": "Remove ❤️", "callback_data": f"remove_from_wishlist_{wishlist_id}"},
                    ]
                ]
            }
        return {
            "inline_keyboard": [
                [
                    {"text": "Start Bid", "callback_data": f"start_bid_{listing_id}"},
                    {"text": "Make Offer ", "callback_data": f"make_auction_offer_{listing_id}"},
                    {"text": "Remove ❤️", "callback_data": f"remove_from_wishlist_{wishlist_id}"},
                ]
            ]
        }

    @staticmethod
    def create_bid_amount_buttons(listing_id: int, min_bid: int = 5) -> Dict[str, Any]:
        """Exact dynamic bid increment buttons plus Cancel."""
        if min_bid <= 0:
            min_bid = 5
        amounts = [min_bid * i for i in range(1, 7)]
        rows = [[{"text": f"${amt}", "callback_data": f"bid_amount_{listing_id}_{amt}"}] for amt in amounts]
        rows.append([{"text": "Cancel", "callback_data": "cancel_process"}])
        return {"inline_keyboard": rows}

    @staticmethod
    def bid_placed_success_buttons(listing_id: int) -> Dict[str, Any]:
        """Exact buttons shown after a bid is accepted."""
        return {
            "inline_keyboard": [
                [
                    {"text": "Refresh", "callback_data": f"refresh_auction_listing_{listing_id}"},
                    {"text": "Add Bid", "callback_data": f"start_bid_{listing_id}"},
                ]
            ]
        }

    @staticmethod
    def update_buyer_contact_details_button() -> Dict[str, Any]:
        """Exact inline button for updating delivery address."""
        return {
            "inline_keyboard": [
                [{"text": "Update Details", "callback_data": "update_buyer_contact_details_"}]
            ]
        }


class SellerKeyboards:
    """Exact keyboards from fish_registration.py."""

    @staticmethod
    def main_menu() -> Dict[str, Any]:
        """Exact legacy ReplyKeyboardMarkup for seller bot."""
        return {
            "keyboard": [
                [{"text": "Start New Listing"}, {"text": "Helpdesk"}],
                [{"text": "My Listings"}, {"text": "Live Listings"}],
                [{"text": "My Closed Listings"}, {"text": "Auction Ending Soon"}],
                [{"text": "Sold Items"}, {"text": "My Wallet"}],
                [{"text": "Join Group"}, {"text": "About"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    @staticmethod
    def breed_options() -> Dict[str, Any]:
        """Category Type (breed) options."""
        return {
            "inline_keyboard": [
                [{"text": breed, "callback_data": breed}] for breed in BREED_OPTIONS
            ]
        }

    @staticmethod
    def category_options() -> Dict[str, Any]:
        """Sales format selection (Auction vs Buy It Now)."""
        return {
            "inline_keyboard": [
                [{"text": opt, "callback_data": opt}] for opt in CATEGORY_OPTIONS
            ]
        }

    @staticmethod
    def end_time_presets() -> Dict[str, Any]:
        """Exact end time duration options for auctions."""
        return {
            "inline_keyboard": [
                [{"text": "Manual Input", "callback_data": "manual_input_auction"}],
                [{"text": "1 day", "callback_data": "1_day_auction"}],
                [{"text": "2 days", "callback_data": "2_days_auction"}],
                [{"text": "3 days", "callback_data": "3_days_auction"}],
                [{"text": "5 days", "callback_data": "5_days_auction"}],
                [{"text": "10 days", "callback_data": "10_days_auction"}],
            ]
        }

    @staticmethod
    def skip_picture() -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Skip Picture", "callback_data": "skip_picture"}]
            ]
        }

    @staticmethod
    def skip_video() -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Skip Video", "callback_data": "skip_video"}]
            ]
        }

    @staticmethod
    def edit_details(url: str) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Edit Details", "url": url}]
            ]
        }

    @staticmethod
    def my_listing_actions(listing_id: int) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": f"Delete listing #{listing_id}", "callback_data": f"delete_{listing_id}"}],
                [{"text": "/start", "callback_data": "trigger_start"}],
            ]
        }

    @staticmethod
    def wallet_add_money(user_id: int) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Add Money", "callback_data": f"add_seller_money_{user_id}"}]
            ]
        }

    @staticmethod
    def wallet_amount_options() -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": f"${opt}", "callback_data": f"wallet_amount_save_{opt}"}]
                for opt in WALLET_AMOUNT_OPTIONS
            ]
        }

    @staticmethod
    def enter_payment_details(user_id: int) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Enter Payment Details", "callback_data": f"give_payment_details_{user_id}"}]
            ]
        }
