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
