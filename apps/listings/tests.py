"""Automated tests for Listings domain models and services."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.listings.models import Listing, ListingStatus, ListingType
from apps.tenants.models import Tenant
from services import listings as listing_service


class ListingDomainTest(TestCase):
    """Tests for Listing model and listing domain services."""

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

    def test_create_listing_success(self):
        """Verify successful creation of a tenant-owned listing."""
        listing = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_100",
            seller_username="seller_my",
            title="Premium Betta Fish",
            description="High quality show betta",
            category="Betta",
            listing_type=ListingType.AUCTION,
            quantity=1,
            metadata={"breed": "Halfmoon", "size": "M"},
        )
        self.assertIsNotNone(listing.id)
        self.assertEqual(listing.tenant, self.tenant_a)
        self.assertEqual(listing.status, ListingStatus.PENDING)
        self.assertEqual(listing.remaining_quantity, 1)
        self.assertEqual(listing.metadata.get("breed"), "Halfmoon")

    def test_listing_remaining_quantity_validation(self):
        """Remaining quantity cannot exceed total quantity."""
        listing = Listing(
            tenant=self.tenant_a,
            seller_id="seller_101",
            title="Invalid Quantity Item",
            quantity=5,
            remaining_quantity=10,
        )
        with self.assertRaises(ValidationError):
            listing.full_clean()

    def test_listing_approval_transition(self):
        """Verify valid and invalid state transitions during approval."""
        listing = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_102",
            title="Pending Item",
        )
        self.assertEqual(listing.status, ListingStatus.PENDING)

        # Valid transition: PENDING -> APPROVED
        approved = listing_service.approve_listing(listing)
        self.assertEqual(approved.status, ListingStatus.APPROVED)

        # Invalid transition: cannot re-approve an already APPROVED listing
        with self.assertRaises(ValidationError):
            listing_service.approve_listing(approved)

    def test_listing_rejection_transition(self):
        """Verify rejection transition with optional reason."""
        listing = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_103",
            title="Item To Reject",
        )
        rejected = listing_service.reject_listing(listing, reason="Violates terms")
        self.assertEqual(rejected.status, ListingStatus.REJECTED)
        self.assertEqual(rejected.metadata.get("rejection_reason"), "Violates terms")

    def test_cross_tenant_listing_isolation(self):
        """Listings of Tenant A must never appear in Tenant B queries."""
        listing_a = listing_service.create_listing(
            tenant=self.tenant_a, seller_id="s1", title="MY Item"
        )
        listing_b = listing_service.create_listing(
            tenant=self.tenant_b, seller_id="s2", title="AU Item"
        )

        a_results = Listing.objects.filter(tenant=self.tenant_a)
        b_results = Listing.objects.for_tenant(self.tenant_b)

        self.assertIn(listing_a, a_results)
        self.assertNotIn(listing_b, a_results)
        self.assertIn(listing_b, b_results)
        self.assertNotIn(listing_a, b_results)


class SellerAndMediaDomainTest(TestCase):
    """Tests for Seller model, status lifecycles, and ListingImage media attachments."""

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
        from apps.telegram_engine.models import TelegramUser
        self.tg_user_a = TelegramUser.objects.create(
            tenant=self.tenant_a,
            telegram_user_id=1001,
            chat_id=1001,
            username="seller_alice",
        )
        self.tg_user_b = TelegramUser.objects.create(
            tenant=self.tenant_b,
            telegram_user_id=1001,
            chat_id=1001,
            username="seller_alice_au",
        )

    def test_seller_creation_and_uniqueness(self):
        """Seller creation verifies uniqueness per tenant and prevents duplicate profiles."""
        from django.db import IntegrityError
        from apps.listings.models import Seller, SellerStatus

        seller_a = Seller.objects.create(
            tenant=self.tenant_a,
            telegram_user=self.tg_user_a,
            seller_id="1001",
            business_name="Alice Betta Farm",
            contact_name="Alice Tan",
            phone="+6012345678",
            status=SellerStatus.ACTIVE,
        )
        self.assertIsNotNone(seller_a.id)

        # Duplicate in same tenant must raise IntegrityError
        from django.db import transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Seller.objects.create(
                    tenant=self.tenant_a,
                    telegram_user=self.tg_user_a,
                    seller_id="1001",
                    business_name="Duplicate Farm",
                    contact_name="Alice Tan",
                    phone="+6012345678",
                )

        # Same telegram_user_id in different tenant (Tenant B) is fully isolated and allowed
        seller_b = Seller.objects.create(
            tenant=self.tenant_b,
            telegram_user=self.tg_user_b,
            seller_id="1001",
            business_name="Alice Australia",
            contact_name="Alice Tan",
            phone="+61412345678",
        )
        self.assertIsNotNone(seller_b.id)
        self.assertEqual(Seller.objects.filter(tenant=self.tenant_a).count(), 1)
        self.assertEqual(Seller.objects.filter(tenant=self.tenant_b).count(), 1)

    def test_listing_image_attachment(self):
        """ListingImage attaches to listing with order preserving gallery sequence."""
        from apps.listings.models import ListingImage

        listing = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="1001",
            title="Kohaku High Grade",
        )
        img1 = listing_service.attach_listing_image(
            listing=listing,
            file_url="tenants/cyg-my/listings/img1.jpg",
            telegram_file_id="tg_123",
            order=1,
        )
        img2 = listing_service.attach_listing_image(
            listing=listing,
            file_url="tenants/cyg-my/listings/img2.jpg",
            telegram_file_id="tg_456",
            order=2,
        )

        images = list(listing.images.all())
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].telegram_file_id, "tg_123")
        self.assertEqual(images[1].telegram_file_id, "tg_456")

    def test_live_listing_view(self):
        """Test public /live-listing/ endpoint returns 200 OK with active auctions."""
        from django.test import Client
        from apps.bidding.models import Auction, AuctionStatus
        from django.utils import timezone

        listing = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="1001",
            seller_username="seller1",
            title="Live Betta Halfmoon",
            status=ListingStatus.APPROVED,
        )
        auction = Auction.objects.create(
            tenant=self.tenant_a,
            listing=listing,
            starting_price=20,
            current_price=25,
            start_at=timezone.now() - timezone.timedelta(hours=1),
            end_at=timezone.now() + timezone.timedelta(days=2),
            status=AuctionStatus.ACTIVE,
        )

        client = Client()
        resp = client.get("/live-listing/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Live Betta Halfmoon")
        self.assertContains(resp, "Live Marketplace")

        # Test with seller_id filter
        resp_seller = client.get("/live-listing/1001/")
        self.assertEqual(resp_seller.status_code, 200)
        self.assertContains(resp_seller, "Live Betta Halfmoon")


