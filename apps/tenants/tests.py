"""Automated tests for Phase 02 Multi-Tenant Database Architecture.

Verifies:
A. Tenant creation
B. Tenant slug uniqueness
C. Tenant code uniqueness
D. Tenant activation status
E. Tenant-owned model relationship
F. Tenant ownership persistence
G. Composite uniqueness
H. Tenant query filtering
I. Cross-tenant record separation
J. Migration and check validity
K. Timezone validation (IANA timezone handling)
L. Foreign key deletion protection (on_delete=models.PROTECT)
"""
import subprocess
import sys
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, IntegrityError, models
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.tenants.models import Tenant, TenantOwnedModel


# Concrete test model strictly used to verify TenantOwnedModel behavior
class ConcreteTenantItem(TenantOwnedModel):
    item_code = models.CharField(max_length=50)
    title = models.CharField(max_length=100)

    class Meta:
        app_label = "tenants"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item_code"],
                name="test_item_tenant_code_uniq",
            )
        ]


class TenantModelTest(TestCase):
    """Tests for the primary Tenant model."""

    def test_tenant_creation_success(self):
        """A. Tenant creation with valid attributes succeeds."""
        tenant = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-malaysia",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
            is_active=True,
        )
        self.assertIsNotNone(tenant.id)
        self.assertEqual(str(tenant), "CYG Malaysia (MY)")
        self.assertEqual(tenant.slug, "cyg-malaysia")
        self.assertEqual(tenant.code, "MY")
        self.assertEqual(tenant.currency, "MYR")
        self.assertTrue(tenant.is_active)

    def test_tenant_slug_uniqueness(self):
        """B. Duplicate tenant slugs must fail uniqueness constraint at model and DB level."""
        Tenant.objects.create(
            name="CYG Malaysia",
            slug="shared-slug",
            code="MY1",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )
        duplicate = Tenant(
            name="AquaBid Australia",
            slug="shared-slug",
            code="AU1",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )
        # Model-level validation triggers ValidationError
        with self.assertRaises(ValidationError):
            duplicate.save()

        # Database-level constraint triggers IntegrityError if full_clean is bypassed
        with self.assertRaises(IntegrityError):
            super(Tenant, duplicate).save()

    def test_tenant_code_uniqueness(self):
        """C. Duplicate tenant codes must fail uniqueness constraint at model and DB level."""
        Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )
        duplicate = Tenant(
            name="Another Malaysia Entity",
            slug="cyg-my-2",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )
        # Model-level validation triggers ValidationError
        with self.assertRaises(ValidationError):
            duplicate.save()

        # Database-level constraint triggers IntegrityError if full_clean is bypassed
        with self.assertRaises(IntegrityError):
            super(Tenant, duplicate).save()

    def test_tenant_activation_status(self):
        """D. Tenant is_active flag operates cleanly."""
        t1 = Tenant.objects.create(
            name="Active Tenant",
            slug="active-tenant",
            code="ACT",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
            is_active=True,
        )
        t2 = Tenant.objects.create(
            name="Inactive Tenant",
            slug="inactive-tenant",
            code="INA",
            country="New Zealand",
            timezone="Pacific/Auckland",
            currency="NZD",
            is_active=False,
        )
        active_tenants = Tenant.objects.filter(is_active=True)
        self.assertIn(t1, active_tenants)
        self.assertNotIn(t2, active_tenants)

    def test_timezone_validation(self):
        """K. Timezone must be a valid IANA identifier, rejecting invalid text."""
        # Valid timezones
        for tz in ["Australia/Sydney", "Asia/Kuala_Lumpur", "UTC", "America/New_York"]:
            tenant = Tenant(
                name=f"Tenant {tz}",
                slug=f"slug-{tz.replace('/', '-').lower()}",
                code=f"C{abs(hash(tz)) % 10000}",
                country="Country",
                timezone=tz,
                currency="USD",
            )
            # Should not raise
            tenant.clean()

        # Invalid timezone
        invalid_tenant = Tenant(
            name="Invalid TZ Tenant",
            slug="invalid-tz",
            code="BADTZ",
            country="Country",
            timezone="Invalid/Timezone_String",
            currency="USD",
        )
        with self.assertRaises(ValidationError):
            invalid_tenant.clean()


class TenantIsolationAndOwnershipTest(TestCase):
    """Tests verifying database-level tenant isolation and query behavior."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Dynamically create the database table for ConcreteTenantItem test model
        with connection.schema_editor() as editor:
            editor.create_model(ConcreteTenantItem)

    @classmethod
    def tearDownClass(cls):
        # Cleanly drop test table
        with connection.schema_editor() as editor:
            editor.delete_model(ConcreteTenantItem)
        super().tearDownClass()

    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            name="Tenant Australia",
            slug="australia",
            code="AU",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
        )
        self.tenant_b = Tenant.objects.create(
            name="Tenant Malaysia",
            slug="malaysia",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
        )

    def test_tenant_owned_relationship_and_persistence(self):
        """E & F. Records correctly link to their respective tenant and persist."""
        item_a = ConcreteTenantItem.objects.create(
            tenant=self.tenant_a,
            item_code="LOT-101",
            title="Australian Item",
        )
        item_b = ConcreteTenantItem.objects.create(
            tenant=self.tenant_b,
            item_code="LOT-201",
            title="Malaysian Item",
        )

        item_a.refresh_from_db()
        item_b.refresh_from_db()

        self.assertEqual(item_a.tenant, self.tenant_a)
        self.assertEqual(item_b.tenant, self.tenant_b)
        self.assertNotEqual(item_a.tenant, item_b.tenant)

    def test_composite_uniqueness_per_tenant(self):
        """G. Identical item_code is permitted across DIFFERENT tenants, but rejected within the SAME tenant."""
        # Same item code allowed across different tenants
        item_a = ConcreteTenantItem.objects.create(
            tenant=self.tenant_a,
            item_code="SHARED-ITEM-01",
            title="A Item",
        )
        item_b = ConcreteTenantItem.objects.create(
            tenant=self.tenant_b,
            item_code="SHARED-ITEM-01",
            title="B Item",
        )
        self.assertEqual(item_a.item_code, item_b.item_code)

        # Duplicate item code under the SAME tenant violates composite uniqueness
        with self.assertRaises(IntegrityError):
            ConcreteTenantItem.objects.create(
                tenant=self.tenant_a,
                item_code="SHARED-ITEM-01",
                title="Duplicate under Tenant A",
            )

    def test_tenant_query_filtering_and_cross_tenant_isolation(self):
        """H & I. Explicit tenant queries guarantee strict record isolation."""
        ConcreteTenantItem.objects.create(
            tenant=self.tenant_a, item_code="A-1", title="Item A1"
        )
        ConcreteTenantItem.objects.create(
            tenant=self.tenant_a, item_code="A-2", title="Item A2"
        )
        ConcreteTenantItem.objects.create(
            tenant=self.tenant_b, item_code="B-1", title="Item B1"
        )

        # Query using explicit filter
        a_records = ConcreteTenantItem.objects.filter(tenant=self.tenant_a)
        b_records = ConcreteTenantItem.objects.filter(tenant=self.tenant_b)

        self.assertEqual(a_records.count(), 2)
        self.assertEqual(b_records.count(), 1)
        self.assertTrue(all(r.tenant == self.tenant_a for r in a_records))
        self.assertTrue(all(r.tenant == self.tenant_b for r in b_records))

        # Query using custom manager .for_tenant helper
        a_helper_records = ConcreteTenantItem.objects.for_tenant(self.tenant_a)
        self.assertEqual(list(a_records), list(a_helper_records))

    def test_deletion_protection(self):
        """L. Deleting a Tenant with active tenant-owned records must be blocked by models.PROTECT."""
        ConcreteTenantItem.objects.create(
            tenant=self.tenant_a, item_code="A-KEEP", title="Protected Item"
        )
        with self.assertRaises(ProtectedError):
            self.tenant_a.delete()

        # Verify tenant still exists in database
        self.assertTrue(Tenant.objects.filter(id=self.tenant_a.id).exists())


class MigrationAndSystemCheckTest(TestCase):
    """J. Test Django system check and migration status."""

    def test_system_check_passes(self):
        """Django system check must report 0 issues."""
        call_command("check")

    def test_migrations_are_in_sync(self):
        """makemigrations in a clean process must detect no unmigrated model changes."""
        result = subprocess.run(
            [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Unapplied model changes detected:\n{result.stdout}\n{result.stderr}",
        )


class TenantMembershipSecurityTest(TestCase):
    """Unit tests for TenantMembership model and apps.tenants.security authorization functions."""

    def setUp(self):
        from django.contrib.auth.models import User
        from apps.tenants.models import TenantMembership, TenantRole
        from apps.tenants.security import (
            is_platform_admin,
            is_tenant_admin,
            is_tenant_staff,
            can_switch_tenant,
            get_user_tenants,
            resolve_requested_tenant,
            enforce_tenant_object_access,
        )

        self.tenant_a = Tenant.objects.create(
            name="Alpha Corp", slug="alpha", code="AL", country="SG", timezone="UTC", currency="SGD"
        )
        self.tenant_b = Tenant.objects.create(
            name="Beta Corp", slug="beta", code="BE", country="JP", timezone="UTC", currency="JPY"
        )

        self.superuser = User.objects.create_superuser("root_admin", "root@platform.io", "pass123")
        self.user_a_admin = User.objects.create_user("a_admin", "a_admin@alpha.io", "pass123", is_staff=True)
        TenantMembership.objects.create(user=self.user_a_admin, tenant=self.tenant_a, role=TenantRole.TENANT_ADMIN)

        self.user_a_staff = User.objects.create_user("a_staff", "a_staff@alpha.io", "pass123", is_staff=True)
        TenantMembership.objects.create(user=self.user_a_staff, tenant=self.tenant_a, role=TenantRole.TENANT_STAFF)

        self.user_b_admin = User.objects.create_user("b_admin", "b_admin@beta.io", "pass123", is_staff=True)
        TenantMembership.objects.create(user=self.user_b_admin, tenant=self.tenant_b, role=TenantRole.TENANT_ADMIN)

    def test_platform_admin_recognition(self):
        """Superuser is always recognized as platform admin and can switch tenants."""
        from apps.tenants.security import is_platform_admin, can_switch_tenant
        self.assertTrue(is_platform_admin(self.superuser))
        self.assertTrue(can_switch_tenant(self.superuser))
        self.assertFalse(is_platform_admin(self.user_a_admin))
        self.assertFalse(can_switch_tenant(self.user_a_admin))

    def test_tenant_admin_and_staff_roles(self):
        """Role helpers accurately distinguish between tenant admin and staff."""
        from apps.tenants.security import is_tenant_admin, is_tenant_staff
        # Admin has admin rights and staff rights
        self.assertTrue(is_tenant_admin(self.user_a_admin, self.tenant_a))
        self.assertTrue(is_tenant_staff(self.user_a_admin, self.tenant_a))
        # Staff has staff rights but NOT admin rights
        self.assertFalse(is_tenant_admin(self.user_a_staff, self.tenant_a))
        self.assertTrue(is_tenant_staff(self.user_a_staff, self.tenant_a))

        # Cross-tenant role check returns False
        self.assertFalse(is_tenant_admin(self.user_a_admin, self.tenant_b))
        self.assertFalse(is_tenant_staff(self.user_a_staff, self.tenant_b))

    def test_resolve_requested_tenant(self):
        """Platform admin can resolve any tenant; Tenant admin cannot resolve foreign tenant."""
        from django.core.exceptions import PermissionDenied
        from apps.tenants.security import resolve_requested_tenant

        # Platform admin requested Tenant B -> allowed
        resolved = resolve_requested_tenant(self.superuser, self.tenant_b.id)
        self.assertEqual(resolved, self.tenant_b)

        # Tenant A admin requesting Tenant A -> allowed
        resolved_a = resolve_requested_tenant(self.user_a_admin, self.tenant_a.id)
        self.assertEqual(resolved_a, self.tenant_a)

        # Tenant A admin requesting Tenant B -> PermissionDenied
        with self.assertRaises(PermissionDenied):
            resolve_requested_tenant(self.user_a_admin, self.tenant_b.id)

    def test_enforce_tenant_object_access(self):
        """enforce_tenant_object_access raises Http404 on cross-tenant mismatch to avoid enumeration."""
        from django.http import Http404
        from apps.tenants.security import enforce_tenant_object_access

        class DummyObj:
            def __init__(self, tenant):
                self.tenant = tenant

        obj_a = DummyObj(self.tenant_a)
        obj_b = DummyObj(self.tenant_b)

        # Same tenant -> passes
        enforce_tenant_object_access(obj_a, self.tenant_a)

        # Mismatched tenant -> raises Http404
        with self.assertRaises(Http404):
            enforce_tenant_object_access(obj_b, self.tenant_a)


class SubdomainMiddlewareTest(TestCase):
    """Tests for SubdomainTenantMiddleware."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
            is_active=True,
        )
        self.inactive_tenant = Tenant.objects.create(
            name="Inactive Aqua",
            slug="inactive",
            code="INACT",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
            is_active=False,
        )

    def test_admin_subdomain_resolution(self):
        """admin.auctionbot.shop sets is_super_admin_host=True and redirects root to /super-admin/."""
        response = self.client.get("/", HTTP_HOST="admin.auctionbot.shop")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/super-admin/")

    def test_tenant_subdomain_resolution(self):
        """cyg.auctionbot.shop resolves active tenant and redirects root to /dashboard/."""
        response = self.client.get("/", HTTP_HOST="cyg.auctionbot.shop")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/dashboard/")

    def test_unknown_subdomain_404(self):
        """nonexistent.auctionbot.shop returns 404 with tenant_not_found page."""
        response = self.client.get("/", HTTP_HOST="nonexistent.auctionbot.shop")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Organization Not Found", status_code=404)

    def test_inactive_subdomain_403(self):
        """inactive.auctionbot.shop returns 403 with tenant_inactive page."""
        response = self.client.get("/", HTTP_HOST="inactive.auctionbot.shop")
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Suspended", status_code=403)


class SuperAdminPortalTest(TestCase):
    """Tests for the Platform Super Admin Portal."""

    def setUp(self):
        from django.contrib.auth.models import User
        from apps.tenants.models import TenantMembership, TenantRole
        from apps.telegram_engine.models import BotType

        self.superuser = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@auctionbot.shop",
            password="SuperPassword@786",
        )
        self.tenant = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
            admin_username="cyg_admin",
            admin_initial_password="InitialPassword@123",
            is_active=True,
        )

    def test_unauthenticated_access_redirects(self):
        """Accessing /super-admin/ without auth redirects to /super-admin/login/."""
        response = self.client.get("/super-admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/super-admin/login/", response.url)

    def test_super_admin_login_and_dashboard(self):
        """Super admin logs in and views dashboard with tenant password displayed."""
        login_resp = self.client.post("/super-admin/login/", {
            "username": "superadmin",
            "password": "SuperPassword@786",
        })
        self.assertEqual(login_resp.status_code, 302)

        dash_resp = self.client.get("/super-admin/")
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "CYG Malaysia")
        self.assertContains(dash_resp, "InitialPassword@123")

    def test_register_tenant_creates_dual_bots_and_credentials(self):
        """Registering a new tenant provisions admin user, membership, and dual Telegram bots."""
        from apps.telegram_engine.models import TelegramBotConfig, BotType

        self.client.login(username="superadmin", password="SuperPassword@786")
        post_data = {
            "name": "AquaBid Australia",
            "slug": "aquabid",
            "code": "AU",
            "country": "Australia",
            "currency": "AUD",
            "timezone": "Australia/Sydney",
            "admin_username": "aquabid_admin",
            "admin_password": "AquaPassword@2026",
            "seller_bot_username": "AquaBidSellerBot",
            "seller_bot_token": "123456:ABC-SellerToken",
            "bidding_bot_username": "AquaBidAuctionBot",
            "bidding_bot_token": "654321:XYZ-BiddingToken",
        }
        resp = self.client.post("/super-admin/tenants/register/", post_data)
        self.assertEqual(resp.status_code, 302)

        new_tenant = Tenant.objects.get(slug="aquabid")
        self.assertEqual(new_tenant.admin_username, "aquabid_admin")
        self.assertEqual(new_tenant.admin_initial_password, "AquaPassword@2026")

        # Verify dual bots
        seller_bot = TelegramBotConfig.objects.get(tenant=new_tenant, bot_type=BotType.SELLER)
        bidding_bot = TelegramBotConfig.objects.get(tenant=new_tenant, bot_type=BotType.BUYER)
        self.assertEqual(seller_bot.bot_username, "AquaBidSellerBot")
        self.assertEqual(seller_bot.get_token(), "123456:ABC-SellerToken")
        self.assertEqual(bidding_bot.bot_username, "AquaBidAuctionBot")
        self.assertEqual(bidding_bot.get_token(), "654321:XYZ-BiddingToken")

    def test_toggle_tenant_active(self):
        """Super admin can suspend and reactivate tenants."""
        self.client.login(username="superadmin", password="SuperPassword@786")
        self.assertTrue(self.tenant.is_active)

        self.client.post(f"/super-admin/tenants/{self.tenant.id}/toggle/")
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.is_active)

        self.client.post(f"/super-admin/tenants/{self.tenant.id}/toggle/")
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.is_active)


class TenantLoginIsolationTest(TestCase):
    """Tests for tenant isolation during login."""

    def setUp(self):
        from django.contrib.auth.models import User
        from apps.tenants.models import TenantMembership, TenantRole

        self.tenant_a = Tenant.objects.create(
            name="CYG Malaysia", slug="cyg", code="MY",
            country="Malaysia", timezone="Asia/Kuala_Lumpur", currency="MYR", is_active=True,
        )
        self.tenant_b = Tenant.objects.create(
            name="AquaBid Australia", slug="aquabid", code="AU",
            country="Australia", timezone="Australia/Sydney", currency="AUD", is_active=True,
        )

        self.user_a = User.objects.create_user(
            username="cyg_user", password="Password@123", is_staff=True
        )
        TenantMembership.objects.create(
            user=self.user_a, tenant=self.tenant_a, role=TenantRole.TENANT_ADMIN, is_active=True
        )

    def test_user_can_login_to_own_tenant_subdomain(self):
        """User A can log into cyg.auctionbot.shop."""
        response = self.client.post(
            "/dashboard/login/",
            {"username": "cyg_user", "password": "Password@123"},
            HTTP_HOST="cyg.auctionbot.shop",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/dashboard/")

    def test_user_cannot_login_to_foreign_tenant_subdomain(self):
        """User A is rejected when attempting to log into aquabid.auctionbot.shop."""
        response = self.client.post(
            "/dashboard/login/",
            {"username": "cyg_user", "password": "Password@123"},
            HTTP_HOST="aquabid.auctionbot.shop",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access denied: Your account is not authorized to access AquaBid Australia.")

