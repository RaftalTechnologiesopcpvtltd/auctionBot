"""Automated tests for Auction and Bidding domain models and services."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.bidding.models import Auction, AuctionStatus, Bid
from apps.listings.models import ListingStatus, ListingType
from apps.tenants.models import Tenant
from services import auctions as auction_service
from services import bids as bid_service
from services import listings as listing_service


class AuctionAndBiddingDomainTest(TestCase):
    """Comprehensive test suite for Auction, Bid, and associated domain services."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )
        self.tenant_b = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-au",
            code="AU",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )
        self.listing_a = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_1",
            seller_username="malaysia_seller",
            title="Champion Betta",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(self.listing_a)

        self.now = timezone.now()
        self.start_at = self.now - timedelta(hours=1)
        self.end_at = self.now + timedelta(hours=23)

    def test_create_auction_success(self):
        """Verify successful auction creation with proper pricing and date ranges."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
            bid_increment=Decimal("5.00"),
            buy_now_price=Decimal("200.00"),
        )
        self.assertIsNotNone(auction.id)
        self.assertEqual(auction.tenant, self.tenant_a)
        self.assertEqual(auction.status, AuctionStatus.SCHEDULED)
        self.assertEqual(auction.starting_price, Decimal("50.00"))
        self.assertEqual(auction.min_next_bid, Decimal("50.00"))

    def test_auction_date_validation(self):
        """Auction end time must strictly follow start time."""
        with self.assertRaises(ValidationError):
            auction_service.create_auction(
                listing=self.listing_a,
                starting_price=Decimal("50.00"),
                start_at=self.now + timedelta(hours=5),
                end_at=self.now + timedelta(hours=2),  # Invalid: end before start
            )

    def test_auction_mismatched_tenant_rejection(self):
        """An auction cannot link to a listing belonging to a different tenant."""
        listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_2",
            title="AU Listing",
        )
        auction = Auction(
            tenant=self.tenant_a,  # Mismatched: Tenant A vs Listing on Tenant B
            listing=listing_b,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        with self.assertRaises(ValidationError):
            auction.full_clean()

    def test_bid_placement_and_increment_tracking(self):
        """Verify sequential bidding, minimum increments, and leading price updates."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
            bid_increment=Decimal("10.00"),
        )
        auction_service.start_auction(auction, as_of=self.now)

        # 1. First bid must be at least starting_price ($50.00)
        with self.assertRaises(ValidationError):
            bid_service.place_bid(auction, bidder_id="buyer_1", amount=Decimal("40.00"))

        bid_1, auction = bid_service.place_bid(
            auction, bidder_id="buyer_1", bidder_name="Alice", amount=Decimal("50.00")
        )
        self.assertEqual(bid_1.amount, Decimal("50.00"))
        self.assertEqual(auction.current_price, Decimal("50.00"))
        self.assertEqual(auction.highest_bid, bid_1)
        self.assertEqual(auction.min_next_bid, Decimal("60.00"))  # $50 + $10 increment

        # 2. Second bid below $60.00 must be rejected
        with self.assertRaises(ValidationError):
            bid_service.place_bid(auction, bidder_id="buyer_2", amount=Decimal("55.00"))

        # 3. Second bid at or above $60.00 succeeds
        bid_2, auction = bid_service.place_bid(
            auction, bidder_id="buyer_2", bidder_name="Bob", amount=Decimal("75.00")
        )
        self.assertEqual(auction.current_price, Decimal("75.00"))
        self.assertEqual(auction.highest_bid, bid_2)
        self.assertEqual(auction.min_next_bid, Decimal("85.00"))

    def test_seller_cannot_bid_on_own_listing(self):
        """Seller self-bidding (shill bidding) is strictly rejected."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        auction_service.start_auction(auction, as_of=self.now)

        # listing_a seller_id is "seller_1"
        with self.assertRaises(ValidationError) as ctx:
            bid_service.place_bid(auction, bidder_id="seller_1", amount=Decimal("50.00"))
        self.assertIn("Sellers are strictly prohibited", str(ctx.exception))

    def test_bidding_rejected_when_not_active(self):
        """Bids are rejected if the auction is not in ACTIVE status or window is closed."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.now + timedelta(hours=1),
            end_at=self.now + timedelta(hours=10),
        )
        # Auction is in SCHEDULED status
        with self.assertRaises(ValidationError):
            bid_service.place_bid(auction, bidder_id="buyer_1", amount=Decimal("50.00"))

    def test_buy_now_auto_close(self):
        """A bid matching or exceeding buy_now_price immediately sells and closes the auction."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            buy_now_price=Decimal("150.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        auction_service.start_auction(auction, as_of=self.now)

        bid, auction = bid_service.place_bid(
            auction, bidder_id="buyer_rich", bidder_name="Charlie", amount=Decimal("150.00")
        )
        self.assertEqual(auction.status, AuctionStatus.SOLD)
        self.assertEqual(auction.winner_id, "buyer_rich")
        self.assertEqual(auction.winning_price, Decimal("150.00"))
        self.assertEqual(auction.listing.remaining_quantity, 0)

    def test_close_auction_with_winner(self):
        """Closing an auction with valid bids sets SOLD status and designates the winner."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        auction_service.start_auction(auction, as_of=self.now)
        bid_service.place_bid(auction, bidder_id="winner_user", amount=Decimal("80.00"))

        closed_auction = auction_service.close_auction(auction)
        self.assertEqual(closed_auction.status, AuctionStatus.SOLD)
        self.assertEqual(closed_auction.winner_id, "winner_user")
        self.assertEqual(closed_auction.winning_price, Decimal("80.00"))

    def test_close_auction_without_bids_is_unsold(self):
        """Closing an auction with zero bids sets UNSOLD status."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        auction_service.start_auction(auction, as_of=self.now)

        closed_auction = auction_service.close_auction(auction)
        self.assertEqual(closed_auction.status, AuctionStatus.UNSOLD)
        self.assertIsNone(closed_auction.winner_id)
        self.assertIsNone(closed_auction.winning_price)

    def test_cross_tenant_auction_isolation(self):
        """Auctions and bids are strictly isolated by tenant."""
        listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_au",
            title="AU Coral",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(listing_b)

        auction_a = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("30.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        auction_b = auction_service.create_auction(
            listing=listing_b,
            starting_price=Decimal("70.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )

        a_auctions = Auction.objects.filter(tenant=self.tenant_a)
        b_auctions = Auction.objects.for_tenant(self.tenant_b)

        self.assertIn(auction_a, a_auctions)
        self.assertNotIn(auction_b, a_auctions)
        self.assertIn(auction_b, b_auctions)
        self.assertNotIn(auction_a, b_auctions)

    def test_protected_deletion_on_auction(self):
        """Deleting a listing with an existing Auction is blocked by models.PROTECT."""
        auction = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
        )
        with self.assertRaises(ProtectedError):
            self.listing_a.delete()
        self.assertTrue(Auction.objects.filter(id=auction.id).exists())
