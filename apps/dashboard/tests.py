"""Phase 07 & 07.5 Management Dashboard Security & Tenant Isolation Test Suite.

Verifies:
1. Authentication:
   - Anonymous access redirected to login
   - Non-staff users rejected
   - Staff/Admin authenticated access allowed
   - Login and Logout flows
2. Tenant Security Hardening (Phase 07.5):
   - Platform admin can switch tenant context arbitrarily
   - Tenant admin cannot switch tenant context (403 Forbidden)
   - Tenant staff cannot switch tenant context (403 Forbidden)
   - Forged session tenant ID is overridden to authorized tenant
   - Financial authorization: Staff cannot issue refunds/reversals (403 Forbidden)
   - Financial authorization: Tenant admin can issue refunds
   - Business settings authorization: Staff cannot edit business profile (403 Forbidden)
3. Cross-Tenant IDOR Protection:
   - Accessing another tenant's Auction returns 404
   - Approving/Cancelling another tenant's Auction returns 404
   - Accessing another tenant's Listing returns 404
   - Approving/Rejecting another tenant's Listing returns 404
   - Accessing another tenant's Financial Transaction returns 404
   - Refunding another tenant's Transaction returns 404
4. Domain Scoping & Secrets Protection:
   - Metrics and lists only display active tenant data
   - Telegram bot tokens are never rendered in plain text
"""
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.tenants.models import Tenant, TenantMembership, TenantRole
from apps.listings.models import Listing, ListingStatus, ListingType
from apps.bidding.models import Auction, AuctionStatus, Bid
from apps.finance.models import (
    FinancialAccount,
    LedgerTransaction,
    AccountType,
    AccountStatus,
    TransactionType,
    TransactionStatus,
)
from apps.finance.services import (
    create_account,
    deposit,
    get_system_account,
)
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser, TelegramUpdateLog
from services import auctions as auction_service
from services import listings as listing_service


class DashboardAuthenticationAndAuthorizationTests(TestCase):
    """Tests for dashboard session authentication, permissions, and tenant switching."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-au",
            code="AU",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )
        self.tenant_b = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )

        # Platform Admin (Superuser)
        self.platform_admin = User.objects.create_user(
            username="platform_boss",
            email="boss@platform.com",
            password="SecurePassword123!",
            is_staff=True,
            is_superuser=True,
        )

        # Tenant A Admin
        self.tenant_a_admin = User.objects.create_user(
            username="tenant_a_admin",
            email="admin@aquabid.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.tenant_a_admin,
            tenant=self.tenant_a,
            role=TenantRole.TENANT_ADMIN,
        )

        # Tenant A Staff
        self.tenant_a_staff = User.objects.create_user(
            username="tenant_a_staff",
            email="staff@aquabid.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.tenant_a_staff,
            tenant=self.tenant_a,
            role=TenantRole.TENANT_STAFF,
        )

        # Regular non-staff user
        self.regular_user = User.objects.create_user(
            username="regular_user",
            email="user@aquabid.com",
            password="SecurePassword123!",
            is_staff=False,
        )

        self.client = Client()

    def test_anonymous_user_redirected_to_login(self):
        """Unauthenticated requests to protected dashboard routes redirect to login."""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_non_staff_user_rejected(self):
        """Authenticated non-staff users are denied access to management dashboard."""
        self.client.login(username="regular_user", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_staff_user_access_granted(self):
        """Authenticated tenant staff users are granted dashboard access to their tenant."""
        self.client.login(username="tenant_a_staff", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AquaBid")

    def test_login_flow(self):
        """Valid credentials authenticate user and redirect to dashboard home."""
        response = self.client.post(
            reverse("dashboard:login"),
            {"username": "tenant_a_staff", "password": "SecurePassword123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard:home"))

    def test_logout_flow(self):
        """Logging out terminates the session and redirects to login."""
        self.client.login(username="tenant_a_staff", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard:login"))

    def test_platform_admin_can_switch_tenant(self):
        """Platform admins can switch tenant context arbitrarily."""
        self.client.login(username="platform_boss", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:switch_tenant", args=[self.tenant_b.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("active_tenant_id"), self.tenant_b.id)

    def test_tenant_admin_cannot_switch_tenant(self):
        """Tenant admins are strictly forbidden from switching tenant context (403)."""
        self.client.login(username="tenant_a_admin", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:switch_tenant", args=[self.tenant_b.id])
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client.session.get("active_tenant_id"), self.tenant_b.id)

    def test_tenant_staff_cannot_switch_tenant(self):
        """Tenant staff are strictly forbidden from switching tenant context (403)."""
        self.client.login(username="tenant_a_staff", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:switch_tenant", args=[self.tenant_b.id])
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client.session.get("active_tenant_id"), self.tenant_b.id)

    def test_forged_session_tenant_id_reverts_to_authorized_tenant(self):
        """Forging active_tenant_id in session for Tenant B does not bypass authorization."""
        self.client.login(username="tenant_a_admin", password="SecurePassword123!")
        session = self.client.session
        session["active_tenant_id"] = self.tenant_b.id
        session.save()

        # Access dashboard home; decorator must sanitize and resolve back to Tenant A
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AquaBid Australia")
        self.assertNotContains(response, "CYG Malaysia")


class DashboardDomainAndTenantIsolationTests(TestCase):
    """Tests verifying views, metrics, and strict cross-tenant data isolation."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-au",
            code="AU",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )
        self.tenant_b = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )

        self.staff_user = User.objects.create_user(
            username="admin_staff",
            email="staff@aquabid.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.staff_user,
            tenant=self.tenant_a,
            role=TenantRole.TENANT_ADMIN,
        )

        # Bot Config with secret token
        bot_cfg = TelegramBotConfig.objects.create(
            tenant=self.tenant_a,
            bot_username="AquaBidAUBot",
            is_active=True,
        )
        bot_cfg.set_token("123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_SUPER_SECRET")
        bot_cfg.save()

        # Listings
        self.listing_a = listing_service.create_listing(
            tenant=self.tenant_a,
            seller_id="seller_au_1",
            title="Golden Dragon Discus AU",
            description="Premium show grade fish from Melbourne hatchery.",
            category="Discus",
            listing_type=ListingType.AUCTION,
            metadata={"strain": "Golden Dragon"},
        )
        self.listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_my_1",
            title="Red Melon Discus MY",
            description="High grade breeding pair.",
            category="Discus",
            listing_type=ListingType.AUCTION,
            metadata={"strain": "Red Melon"},
        )

        # Auctions
        start_time = timezone.now() - timedelta(minutes=10)
        end_time = timezone.now() + timedelta(hours=2)
        self.auction_a = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("100.00"),
            bid_increment=Decimal("10.00"),
            start_at=start_time,
            end_at=end_time,
        )
        self.auction_a.status = AuctionStatus.ACTIVE
        self.auction_a.save()

        self.auction_b = auction_service.create_auction(
            listing=self.listing_b,
            starting_price=Decimal("200.00"),
            bid_increment=Decimal("20.00"),
            start_at=start_time,
            end_at=end_time,
        )
        self.auction_b.status = AuctionStatus.ACTIVE
        self.auction_b.save()

        # Finance
        deposit(
            tenant=self.tenant_a,
            user_id="user_au_1",
            amount=Decimal("500.00"),
            currency="AUD",
            description="Initial deposit AU",
            idempotency_key="deposit_au_001",
        )

        self.client = Client()
        self.client.login(username="admin_staff", password="SecurePassword123!")

    def test_dashboard_home_renders_tenant_a_data_only(self):
        """Dashboard home KPIs show tenant A metrics and never tenant B data."""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AquaBid Australia")
        self.assertContains(response, "Golden Dragon Discus AU")
        self.assertNotContains(response, "Red Melon Discus MY")

    def test_auctions_list_and_detail(self):
        """Auctions list and detail show only tenant A auctions."""
        response = self.client.get(reverse("dashboard:auctions_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Golden Dragon Discus AU")
        self.assertNotContains(response, "Red Melon Discus MY")

        detail_resp = self.client.get(
            reverse("dashboard:auction_detail", args=[self.auction_a.id])
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Golden Dragon Discus AU")

    def test_listings_list_and_detail(self):
        """Listings views show only tenant A listings."""
        response = self.client.get(reverse("dashboard:listings_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Golden Dragon Discus AU")
        self.assertNotContains(response, "Red Melon Discus MY")

        detail_resp = self.client.get(
            reverse("dashboard:listing_detail", args=[self.listing_a.id])
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Golden Dragon Discus AU")

    def test_users_list_and_create(self):
        """Users management displays user accounts and creates new staff users."""
        response = self.client.get(reverse("dashboard:users_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin_staff")

        # Create new user
        post_resp = self.client.post(
            reverse("dashboard:users_list"),
            {
                "username": "new_moderator",
                "email": "mod@aquabid.com",
                "role": "staff",
                "password": "Password123!",
            },
        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="new_moderator").exists())
        # Verify membership was created and bound to Tenant A
        self.assertTrue(
            TenantMembership.objects.filter(
                user__username="new_moderator",
                tenant=self.tenant_a
            ).exists()
        )

    def test_telegram_security_never_exposes_token_in_plain_text(self):
        """Telegram management views MUST NEVER render the bot token in plain text."""
        overview_resp = self.client.get(reverse("dashboard:telegram_overview"))
        self.assertEqual(overview_resp.status_code, 200)
        self.assertNotContains(overview_resp, "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_SUPER_SECRET")
        self.assertContains(overview_resp, "&bull;")

        settings_resp = self.client.get(reverse("dashboard:telegram_settings"))
        self.assertEqual(settings_resp.status_code, 200)
        self.assertNotContains(settings_resp, "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_SUPER_SECRET")
        self.assertContains(settings_resp, "&bull;")

    def test_telegram_messages_and_users_views(self):
        """Telegram broadcast messages and registered users views return 200."""
        msg_resp = self.client.get(reverse("dashboard:telegram_messages"))
        self.assertEqual(msg_resp.status_code, 200)

        user_resp = self.client.get(reverse("dashboard:telegram_users"))
        self.assertEqual(user_resp.status_code, 200)

    def test_finance_wallets_and_transactions_views(self):
        """Finance views return tenant wallets and double-entry ledger transactions."""
        wallet_resp = self.client.get(reverse("dashboard:finance_wallets"))
        self.assertEqual(wallet_resp.status_code, 200)
        self.assertContains(wallet_resp, "user_au_1")

        tx_resp = self.client.get(reverse("dashboard:finance_transactions"))
        self.assertEqual(tx_resp.status_code, 200)
        self.assertContains(tx_resp, "DEPOSIT")
        self.assertContains(tx_resp, "user_au_1")

    def test_finance_transaction_detail_displays_balanced_entries(self):
        """Transaction detail verifies double-entry ledger debit/credit lines."""
        tx = LedgerTransaction.objects.filter(tenant=self.tenant_a).first()
        self.assertIsNotNone(tx)

        detail_resp = self.client.get(
            reverse("dashboard:finance_transaction_detail", args=[tx.pk])
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "DEBIT")
        self.assertContains(detail_resp, "CREDIT")
        self.assertContains(detail_resp, "500.00")

    def test_reports_and_settings_views(self):
        """Reports and business settings views render with operational data."""
        report_resp = self.client.get(reverse("dashboard:reports"))
        self.assertEqual(report_resp.status_code, 200)
        self.assertContains(report_resp, "Gross Auction Volume")

        settings_resp = self.client.get(reverse("dashboard:settings_business"))
        self.assertEqual(settings_resp.status_code, 200)
        self.assertContains(settings_resp, "AquaBid Australia")

        helpdesk_resp = self.client.get(reverse("dashboard:helpdesk"))
        self.assertEqual(helpdesk_resp.status_code, 200)
        self.assertContains(helpdesk_resp, "Helpdesk & Support Inquiries")


class CrossTenantSecurityAndIDORTests(TestCase):
    """Tests verifying robust IDOR defense and cross-tenant access rejection."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="Tenant A Corp",
            slug="tenant-a",
            code="TA",
            country="Singapore",
            timezone="Asia/Singapore",
            currency="SGD",
        )
        self.tenant_b = Tenant.objects.create(
            name="Tenant B Corp",
            slug="tenant-b",
            code="TB",
            country="Japan",
            timezone="Asia/Tokyo",
            currency="JPY",
        )

        # Tenant A Users
        self.user_a_admin = User.objects.create_user(
            username="admin_a",
            email="admin_a@tenanta.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.user_a_admin,
            tenant=self.tenant_a,
            role=TenantRole.TENANT_ADMIN,
        )

        self.user_a_staff = User.objects.create_user(
            username="staff_a",
            email="staff_a@tenanta.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.user_a_staff,
            tenant=self.tenant_a,
            role=TenantRole.TENANT_STAFF,
        )

        # Tenant B User
        self.user_b_admin = User.objects.create_user(
            username="admin_b",
            email="admin_b@tenantb.com",
            password="SecurePassword123!",
            is_staff=True,
        )
        TenantMembership.objects.create(
            user=self.user_b_admin,
            tenant=self.tenant_b,
            role=TenantRole.TENANT_ADMIN,
        )

        # Tenant B Domain Objects
        self.listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_b_99",
            title="Japanese Koi Kohaku Grade AAA",
            description="Confidential high-value breeder item.",
            category="Koi",
            listing_type=ListingType.AUCTION,
        )

        start_time = timezone.now() - timedelta(minutes=5)
        end_time = timezone.now() + timedelta(hours=1)
        self.auction_b = auction_service.create_auction(
            listing=self.listing_b,
            starting_price=Decimal("1500.00"),
            bid_increment=Decimal("50.00"),
            start_at=start_time,
            end_at=end_time,
        )
        self.auction_b.status = AuctionStatus.ACTIVE
        self.auction_b.save()

        # Tenant B Financial Transaction
        self.tx_b = deposit(
            tenant=self.tenant_b,
            user_id="buyer_b_42",
            amount=Decimal("50000.00"),
            currency="JPY",
            description="Deposit for buyer B",
            idempotency_key="dep_b_42",
        )

        # Tenant A Financial Transaction (for financial authorization test)
        self.tx_a = deposit(
            tenant=self.tenant_a,
            user_id="buyer_a_10",
            amount=Decimal("300.00"),
            currency="SGD",
            description="Deposit for buyer A",
            idempotency_key="dep_a_10",
        )

        self.client = Client()

    def test_tenant_a_cannot_view_tenant_b_auction_detail(self):
        """Accessing Tenant B's auction by ID from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:auction_detail", args=[self.auction_b.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_end_tenant_b_auction(self):
        """Mutating Tenant B's auction from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:auction_end", args=[self.auction_b.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_cancel_tenant_b_auction(self):
        """Cancelling Tenant B's auction from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:auction_cancel", args=[self.auction_b.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_view_tenant_b_listing_detail(self):
        """Accessing Tenant B's listing by ID from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:listing_detail", args=[self.listing_b.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_accept_or_reject_tenant_b_listing(self):
        """Accepting/rejecting Tenant B's listing from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        approve_resp = self.client.post(
            reverse("dashboard:listing_accept", args=[self.listing_b.id])
        )
        self.assertEqual(approve_resp.status_code, 404)

        reject_resp = self.client.post(
            reverse("dashboard:listing_reject", args=[self.listing_b.id])
        )
        self.assertEqual(reject_resp.status_code, 404)

    def test_tenant_a_cannot_view_tenant_b_transaction(self):
        """Accessing Tenant B's ledger transaction detail returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:finance_transaction_detail", args=[self.tx_b.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_refund_tenant_b_transaction(self):
        """Attempting to refund Tenant B's transaction from Tenant A returns 404 Not Found."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:transaction_refund", args=[self.tx_b.id]),
            {"reason": "Malicious cross-tenant refund attempt"},
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_staff_cannot_refund_transaction(self):
        """Tenant staff without admin role cannot issue financial refunds (403 Forbidden)."""
        self.client.login(username="staff_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:transaction_refund", args=[self.tx_a.id]),
            {"reason": "Staff attempt"},
        )
        self.assertEqual(response.status_code, 403)

    def test_tenant_staff_cannot_reverse_transaction(self):
        """Tenant staff without admin role cannot issue financial reversals (403 Forbidden)."""
        self.client.login(username="staff_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:transaction_reverse", args=[self.tx_a.id]),
            {"reason": "Staff reversal attempt"},
        )
        self.assertEqual(response.status_code, 403)

    def test_tenant_admin_can_refund_transaction(self):
        """Tenant admin has financial authorization to issue refunds within their tenant."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:transaction_refund", args=[self.tx_a.id]),
            {"reason": "Authorized admin refund"},
        )
        self.assertEqual(response.status_code, 302)

    def test_tenant_staff_cannot_edit_business_settings(self):
        """Tenant staff cannot edit business settings (403 Forbidden)."""
        self.client.login(username="staff_a", password="SecurePassword123!")
        response = self.client.post(
            reverse("dashboard:settings_business"),
            {"name": "Hacked Tenant Name"},
        )
        self.assertEqual(response.status_code, 403)
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.name, "Tenant A Corp")

    def test_users_list_never_leaks_other_tenant_users(self):
        """Tenant A users list shows only Tenant A users, never Tenant B users."""
        self.client.login(username="admin_a", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:users_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin_a")
        self.assertContains(response, "staff_a")
        self.assertNotContains(response, "admin_b")
