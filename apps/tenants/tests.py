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
