"""Multi-tenant database models for AuctionBot.

Defines:
1. Tenant: Represents an independent regional or organizational entity (e.g. Australia, Malaysia).
2. TenantQuerySet / TenantManager: Provides explicit, non-magic tenant-scoped queries.
3. TenantOwnedModel: Reusable abstract base class enforcing explicit database-level tenant ownership.
"""
import zoneinfo
from django.core.exceptions import ValidationError
from django.db import models


def validate_iana_timezone(value):
    """Ensure timezone is a valid IANA timezone identifier."""
    try:
        zoneinfo.ZoneInfo(value)
    except Exception as exc:
        raise ValidationError(
            f"'{value}' is not a valid IANA timezone identifier (e.g. 'Australia/Sydney', 'Asia/Kuala_Lumpur')."
        ) from exc


class Tenant(models.Model):
    """Represents an independent regional or organizational tenant in the unified platform.

    One Database + One Codebase + Multiple Tenants.
    """

    name = models.CharField(
        max_length=100,
        help_text="Human-readable tenant or organization name (e.g., 'CYG Malaysia', 'AquaBid Australia').",
    )
    slug = models.SlugField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique URL and subdomain identifier (e.g., 'cyg', 'aquabid', 'australia').",
    )
    code = models.CharField(
        max_length=10,
        unique=True,
        db_index=True,
        help_text="Unique uppercase country or region code (e.g., 'MY', 'AU', 'NZ').",
    )
    country = models.CharField(
        max_length=100,
        help_text="Primary country of operations.",
    )
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_iana_timezone],
        help_text="Standard IANA timezone string (e.g., 'Australia/Sydney', 'Asia/Kuala_Lumpur').",
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Standard 3-letter ISO 4217 currency code (e.g., 'AUD', 'MYR', 'USD').",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Controls whether this tenant is operational and accepting traffic.",
    )
    admin_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Primary administrative account username for this tenant.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when the tenant was registered.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the tenant configuration was last modified.",
    )

    @property
    def subdomain_url(self) -> str:
        """Returns the public subdomain URL for this tenant."""
        return f"https://{self.slug}.auctionbot.shop"

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "code"], name="tenant_active_code_idx"),
        ]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.currency:
            self.currency = self.currency.strip().upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})
        if self.slug:
            self.slug = self.slug.strip().lower()
        if self.timezone:
            validate_iana_timezone(self.timezone)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class TenantQuerySet(models.QuerySet):
    """Custom QuerySet enabling explicit tenant filtering."""

    def for_tenant(self, tenant):
        """Filter records belonging strictly to the given Tenant instance or ID."""
        return self.filter(tenant=tenant)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager for TenantOwnedModel instances providing explicit tenant scoping."""
    pass


class TenantOwnedModel(models.Model):
    """Abstract base model establishing mandatory database-level tenant ownership.

    Every tenant-owned business record MUST inherit from this model.
    Enforces on_delete=models.PROTECT to prevent accidental cascading deletion of tenant data.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
        db_index=True,
        help_text="Mandatory explicit foreign key to the owning Tenant.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the record was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the record was last updated.",
    )

    objects = TenantManager()

    class Meta:
        abstract = True


class TenantRole(models.TextChoices):
    PLATFORM_ADMIN = "PLATFORM_ADMIN", "Platform Administrator"
    TENANT_ADMIN = "TENANT_ADMIN", "Tenant Administrator"
    TENANT_STAFF = "TENANT_STAFF", "Tenant Staff"


class TenantMembership(models.Model):
    """Associates an authenticated User with a Tenant and administrative role."""

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
        help_text="User belonging to the tenant.",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="memberships",
        help_text="Tenant organization.",
    )
    role = models.CharField(
        max_length=32,
        choices=TenantRole.choices,
        default=TenantRole.TENANT_STAFF,
        db_index=True,
        help_text="Administrative role within the tenant.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this membership is active.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the membership was granted.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the membership was last updated.",
    )

    class Meta:
        verbose_name = "Tenant Membership"
        verbose_name_plural = "Tenant Memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tenant"],
                name="unique_user_tenant_membership",
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_active", "role"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.tenant.name} ({self.role})"

