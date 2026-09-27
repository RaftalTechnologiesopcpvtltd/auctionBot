import threading
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.bidding.models import Auction, AuctionStatus, Bid
from apps.bidding.tasks import close_auction_task
from apps.listings.models import ListingStatus, ListingType
from apps.tenants.models import Tenant
from services import auctions as auction_service
from services import bids as bid_service
from services.bids import (
    BidTooLowError,
    AuctionClosedError,
    AuctionNotActiveError,
    BidderNotEligibleError,
)
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

        closed_auction = auction_service.close_auction(auction, as_of=self.end_at + timedelta(seconds=1))
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

        closed_auction = auction_service.close_auction(auction, as_of=self.end_at + timedelta(seconds=1))
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


class ConcurrentBiddingEngineTest(TransactionTestCase):
    """High-concurrency race condition and atomic locking tests executed against PostgreSQL."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my-concurrency",
            code="MYC",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )
        self.tenant_b = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-au-concurrency",
            code="AUC",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )

        self.listing_a = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_my",
            title="Premium Betta",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(self.listing_a)

        self.now = timezone.now()
        self.start_at = self.now - timedelta(minutes=30)
        self.end_at = self.now + timedelta(hours=2)

        self.auction_a = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            start_at=self.start_at,
            end_at=self.end_at,
            bid_increment=Decimal("10.00"),
            buy_now_price=Decimal("200.00"),
        )
        auction_service.start_auction(self.auction_a, as_of=self.now)

    def _run_in_thread(self, target, *args, **kwargs):
        """Helper to run a function in a separate thread with independent database connection."""
        res = {"success": False, "val": None, "exc": None}

        def worker():
            try:
                val = target(*args, **kwargs)
                res["success"] = True
                res["val"] = val
            except Exception as e:
                res["exc"] = e
            finally:
                connection.close()

        t = threading.Thread(target=worker)
        return t, res

    def test_concurrent_two_bids_different_amounts(self):
        """Test 1: Two bids with different valid amounts arrive concurrently; both succeed in sequence."""
        t1, res1 = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_alice",
            amount=Decimal("50.00"),
            tenant=self.tenant_a,
        )
        t2, res2 = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_bob",
            amount=Decimal("60.00"),
            tenant=self.tenant_a,
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.auction_a.refresh_from_db()
        # Regardless of which thread acquired the lock first:
        # Final current_price must authoritatively be $60.00 and highest bidder must be buyer_bob
        self.assertEqual(self.auction_a.current_price, Decimal("60.00"))
        self.assertEqual(self.auction_a.highest_bid.bidder_id, "buyer_bob")
        self.assertTrue(res2["success"])

        if res1["success"]:
            # Thread 1 placed $50 first, then Thread 2 placed $60
            self.assertEqual(Bid.objects.filter(auction=self.auction_a).count(), 2)
        else:
            # Thread 2 placed $60 first, so Thread 1's $50 was rejected as below min_next_bid
            self.assertIsInstance(res1["exc"], BidTooLowError)
            self.assertEqual(Bid.objects.filter(auction=self.auction_a).count(), 1)

    def test_concurrent_two_bids_same_amount(self):
        """Test 2: Two bids with the exact same amount arrive simultaneously; exactly one succeeds."""
        t1, res1 = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_1",
            amount=Decimal("50.00"),
            tenant=self.tenant_a,
        )
        t2, res2 = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_2",
            amount=Decimal("50.00"),
            tenant=self.tenant_a,
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in [res1, res2] if r["success"]]
        failures = [r for r in [res1, res2] if not r["success"]]

        # Exactly one bid must succeed, and the other must fail with BidTooLowError
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0]["exc"], BidTooLowError)

        self.auction_a.refresh_from_db()
        self.assertEqual(Bid.objects.filter(auction=self.auction_a).count(), 1)
        self.assertEqual(self.auction_a.current_price, Decimal("50.00"))

    def test_concurrent_bids_near_end_trigger_anti_sniping(self):
        """Test 3: Bids arriving within the anti-sniping window dynamically extend end_at."""
        # Set auction end_at to 60 seconds from now (threshold is 120s)
        near_end = self.now + timedelta(seconds=60)
        self.auction_a.end_at = near_end
        self.auction_a.anti_sniping_seconds = 120
        self.auction_a.extension_seconds = 180
        self.auction_a.save()

        # Place a bid at current time (within threshold)
        bid, updated_auction = bid_service.place_bid(
            auction_or_id=self.auction_a.id,
            bidder_id="sniper_1",
            amount=Decimal("50.00"),
            as_of=self.now,
            tenant=self.tenant_a,
        )

        # Verify end_at was atomically extended by 180 seconds
        expected_end = near_end + timedelta(seconds=180)
        self.assertEqual(updated_auction.end_at, expected_end)
        self.assertEqual(updated_auction.extension_count, 1)

    def test_concurrent_bid_and_close_auction(self):
        """Test 4: One process places a bid while another process attempts to close the auction."""
        # Auction close attempted before end_at has passed must raise ValidationError
        t_bid, res_bid = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_live",
            amount=Decimal("50.00"),
            tenant=self.tenant_a,
        )
        t_close, res_close = self._run_in_thread(
            auction_service.close_auction,
            auction_or_id=self.auction_a.id,
            as_of=self.now,  # end_at has not passed yet
            tenant=self.tenant_a,
        )

        t_bid.start()
        t_close.start()
        t_bid.join()
        t_close.join()

        # Bid succeeds; premature close is rejected because end_at has not passed
        self.assertTrue(res_bid["success"])
        self.assertFalse(res_close["success"])
        self.assertIsInstance(res_close["exc"], ValidationError)

    def test_multiple_concurrent_bids_increasing_amounts(self):
        """Test 5: Multiple concurrent bids with increasing amounts all serialize cleanly."""
        threads = []
        results = []
        amounts = [Decimal("50.00"), Decimal("60.00"), Decimal("70.00"), Decimal("80.00")]

        for i, amt in enumerate(amounts):
            t, r = self._run_in_thread(
                bid_service.place_bid,
                auction_or_id=self.auction_a.id,
                bidder_id=f"bidder_{i}",
                amount=amt,
                tenant=self.tenant_a,
            )
            threads.append(t)
            results.append(r)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.auction_a.refresh_from_db()
        self.assertEqual(self.auction_a.current_price, Decimal("80.00"))
        self.assertEqual(self.auction_a.highest_bid.amount, Decimal("80.00"))
        successful_bids = [r for r in results if r["success"]]
        self.assertEqual(Bid.objects.filter(auction=self.auction_a).count(), len(successful_bids))

    def test_concurrent_buy_now_and_normal_bid(self):
        """Test 6: Concurrent buy-now and normal bid; buy-now locks in SOLD status."""
        # Thread 1: Buy-now bid ($200.00)
        t_buynow, res_buynow = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_instant",
            amount=Decimal("200.00"),
            tenant=self.tenant_a,
        )
        # Thread 2: Normal bid ($60.00)
        t_normal, res_normal = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_late",
            amount=Decimal("60.00"),
            tenant=self.tenant_a,
        )

        t_buynow.start()
        t_normal.start()
        t_buynow.join()
        t_normal.join()

        self.auction_a.refresh_from_db()
        self.assertEqual(self.auction_a.status, AuctionStatus.SOLD)
        self.assertEqual(self.auction_a.winner_id, "buyer_instant")
        self.assertEqual(self.auction_a.winning_price, Decimal("200.00"))

    def test_duplicate_bid_idempotency_key(self):
        """Test 7: Duplicate logical request with the same idempotency_key returns existing bid without duplicate."""
        key = "IDEMP-KEY-TEST-999"
        bid1, auction1 = bid_service.place_bid(
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_idemp",
            amount=Decimal("50.00"),
            idempotency_key=key,
            tenant=self.tenant_a,
        )
        # Repeat submission with exact same idempotency_key
        bid2, auction2 = bid_service.place_bid(
            auction_or_id=self.auction_a.id,
            bidder_id="buyer_idemp",
            amount=Decimal("50.00"),
            idempotency_key=key,
            tenant=self.tenant_a,
        )

        self.assertEqual(bid1.id, bid2.id)
        self.assertEqual(Bid.objects.filter(auction=self.auction_a, idempotency_key=key).count(), 1)

    def test_cross_tenant_concurrent_bids(self):
        """Test 8: Cross-tenant concurrent bids execute independently with zero cross-tenant contamination."""
        listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_au",
            title="AU Coral Lot",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(listing_b)
        auction_b = auction_service.create_auction(
            listing=listing_b,
            starting_price=Decimal("100.00"),
            start_at=self.start_at,
            end_at=self.end_at,
            bid_increment=Decimal("20.00"),
        )
        auction_service.start_auction(auction_b, as_of=self.now)

        t_my, res_my = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=self.auction_a.id,
            bidder_id="bidder_my",
            amount=Decimal("50.00"),
            tenant=self.tenant_a,
        )
        t_au, res_au = self._run_in_thread(
            bid_service.place_bid,
            auction_or_id=auction_b.id,
            bidder_id="bidder_au",
            amount=Decimal("100.00"),
            tenant=self.tenant_b,
        )

        t_my.start()
        t_au.start()
        t_my.join()
        t_au.join()

        self.assertTrue(res_my["success"])
        self.assertTrue(res_au["success"])

        self.auction_a.refresh_from_db()
        auction_b.refresh_from_db()

        self.assertEqual(self.auction_a.current_price, Decimal("50.00"))
        self.assertEqual(auction_b.current_price, Decimal("100.00"))
        self.assertEqual(self.auction_a.highest_bid.bidder_id, "bidder_my")
        self.assertEqual(auction_b.highest_bid.bidder_id, "bidder_au")

    def test_celery_close_auction_task_execution_and_idempotency(self):
        """Test 9: Celery close_auction_task closes expired auction and is strictly idempotent."""
        # Place a leading bid
        bid_service.place_bid(
            auction_or_id=self.auction_a.id,
            bidder_id="winner_sam",
            amount=Decimal("70.00"),
            tenant=self.tenant_a,
        )

        expired_time = self.end_at + timedelta(minutes=5)

        # 1. Execute task
        res1 = close_auction_task.apply(
            kwargs={
                "auction_id": self.auction_a.id,
                "tenant_id": self.tenant_a.id,
                "as_of_iso": expired_time.isoformat(),
            }
        ).get()

        self.assertEqual(res1["status"], "ok")
        self.assertEqual(res1["auction_status"], AuctionStatus.SOLD)
        self.assertEqual(res1["winner_id"], "winner_sam")
        self.assertEqual(res1["winning_price"], "70.00")

        self.auction_a.refresh_from_db()
        self.assertEqual(self.auction_a.status, AuctionStatus.SOLD)
        self.assertEqual(self.auction_a.winner_id, "winner_sam")

        # 2. Re-run task (idempotency test)
        res2 = close_auction_task.apply(
            kwargs={
                "auction_id": self.auction_a.id,
                "tenant_id": self.tenant_a.id,
                "as_of_iso": expired_time.isoformat(),
            }
        ).get()
        self.assertEqual(res2["status"], "ok")
        self.assertEqual(res2["auction_status"], AuctionStatus.SOLD)
        self.assertEqual(self.auction_a.winner_id, "winner_sam")

