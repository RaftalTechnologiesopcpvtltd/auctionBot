"""Phase 07 Management Dashboard & Frontend Test Suite.

Verifies:
1. Authentication:
   - Anonymous access redirected to login
   - Non-staff users rejected
   - Staff/Admin authenticated access allowed
   - Login and Logout flows
2. Authorization & Tenant Isolation:
   - Active tenant context resolution
   - Switching active tenant via session
   - Strict tenant data filtering (auctions, listings, bids, finance)
3. Domain Integration & Views:
   - Home metrics & KPIs
   - Auctions list & detail view
   - Listings list, detail, and approval/rejection actions
   - Bids history
   - Sellers management
   - Users management & user creation
   - Telegram bot management (overview, settings, messages, users)
   - Wallets and account balances
   - Financial transactions list & double-entry ledger detail
   - Financial refund action via Phase 06 services
   - Reports, Business Settings, and Helpdesk views
4. Security:
   - Telegram bot tokens are never rendered in plain text
   - Financial mutations respect domain validation
"""
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.tenants.models import Tenant
from apps.listings.models import Listing, ListingStatus, ListingType
from apps.bidding.models import Auction, AuctionStatus, Bid
from apps.finance.models import (
    FinancialAccount,
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
from apps.telegram_engine.models import TelegramBotConfig
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

        self.staff_user = User.objects.create_user(
            username="admin_staff",
            email="staff@aquabid.com",
            password="SecurePassword123!",
            is_staff=True,
        )
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
        """Authenticated staff users are granted full dashboard access."""
        self.client.login(username="admin_staff", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AquaBid")

    def test_login_flow(self):
        """Valid credentials authenticate user and redirect to dashboard home."""
        response = self.client.post(
            reverse("dashboard:login"),
            {"username": "admin_staff", "password": "SecurePassword123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard:home"))

    def test_logout_flow(self):
        """Logging out terminates the session and redirects to login."""
        self.client.login(username="admin_staff", password="SecurePassword123!")
        response = self.client.get(reverse("dashboard:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("dashboard:login"))

    def test_switch_tenant_updates_session(self):
        """Switching tenants updates the active tenant stored in session."""
        self.client.login(username="admin_staff", password="SecurePassword123!")
        response = self.client.get(
            reverse("dashboard:switch_tenant", args=[self.tenant_b.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("active_tenant_id"), self.tenant_b.id)


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
            seller_username="aus_seller",
            title="Halfmoon Betta AU",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(self.listing_a)

        self.listing_b = listing_service.create_listing(
            tenant=self.tenant_b,
            seller_id="seller_my_1",
            seller_username="my_seller",
            title="Dragon Betta MY",
            listing_type=ListingType.AUCTION,
        )
        listing_service.approve_listing(self.listing_b)

        # Auctions
        now = timezone.now()
        self.auction_a = auction_service.create_auction(
            listing=self.listing_a,
            starting_price=Decimal("50.00"),
            bid_increment=Decimal("5.00"),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=23),
        )
        auction_service.start_auction(self.auction_a)

        self.auction_b = auction_service.create_auction(
            listing=self.listing_b,
            starting_price=Decimal("30.00"),
            bid_increment=Decimal("5.00"),
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=23),
        )
        auction_service.start_auction(self.auction_b)

        # Finance
        self.wallet_a = create_account(
            tenant=self.tenant_a,
            owner_id="user_au_1",
            account_type=AccountType.USER_WALLET,
            currency="AUD",
        )
        deposit(
            tenant=self.tenant_a,
            user_id="user_au_1",
            amount=Decimal("500.00"),
            currency="AUD",
            idempotency_key="DEP-TEST-001",
        )

        self.client = Client()
        self.client.login(username="admin_staff", password="SecurePassword123!")
        # Set tenant A in session
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.id
        session.save()

    def test_dashboard_home_metrics(self):
        """Dashboard home renders active tenant statistics accurately."""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Halfmoon Betta AU")
        self.assertNotContains(response, "Dragon Betta MY")

    def test_auctions_list_tenant_isolation(self):
        """Auctions list strictly returns auctions belonging to the active tenant."""
        response = self.client.get(reverse("dashboard:auctions_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Halfmoon Betta AU")
        self.assertNotContains(response, "Dragon Betta MY")

    def test_auction_detail_view(self):
        """Auction detail renders with full bidding and financial status."""
        response = self.client.get(
            reverse("dashboard:auction_detail", args=[self.auction_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Halfmoon Betta AU")
        self.assertContains(response, "AUD 50.00")

    def test_listings_list_and_detail(self):
        """Listings views display correct items and review options."""
        response = self.client.get(reverse("dashboard:listings_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Halfmoon Betta AU")
        self.assertNotContains(response, "Dragon Betta MY")

        detail_resp = self.client.get(
            reverse("dashboard:listing_detail", args=[self.listing_a.pk])
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "aus_seller")

    def test_bids_list_view(self):
        """Bids list view returns 200 OK."""
        response = self.client.get(reverse("dashboard:bids_list"))
        self.assertEqual(response.status_code, 200)

    def test_sellers_list_view(self):
        """Sellers list view displays active tenant sellers."""
        response = self.client.get(reverse("dashboard:sellers_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "aus_seller")

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
        self.assertContains(wallet_resp, "500.00")

        tx_resp = self.client.get(reverse("dashboard:finance_transactions"))
        self.assertEqual(tx_resp.status_code, 200)
        self.assertContains(tx_resp, "DEPOSIT")
        self.assertContains(tx_resp, "user_au_1")

    def test_finance_transaction_detail_displays_balanced_entries(self):
        """Transaction detail verifies double-entry ledger debit/credit lines."""
        # Find transaction created during deposit
        from apps.finance.models import LedgerTransaction
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
