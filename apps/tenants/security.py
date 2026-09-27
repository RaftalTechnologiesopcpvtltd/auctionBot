"""Centralized Tenant Security & Authorization Module.

Enforces:
1. Strict Role Boundaries:
   - PLATFORM_ADMIN: Full platform scope, authorized to switch tenant context.
   - TENANT_ADMIN: Single assigned tenant scope, cannot switch context.
   - TENANT_STAFF: Single assigned tenant scope, cannot switch context, limited mutative actions.
2. Centralized Tenant Resolution (Section 3):
   - Only platform admins can switch tenant context.
   - Normal tenant users are strictly bound to their assigned tenant.
   - Forged or mismatched tenant requests raise PermissionDenied.
3. Cross-Tenant Object Access Validation (IDOR prevention).
"""
from typing import Optional, Union, Any
from django.core.exceptions import PermissionDenied
from django.http import Http404
from apps.tenants.models import Tenant, TenantMembership, TenantRole


def is_platform_admin(user) -> bool:
    """Checks whether the user holds platform administrator privileges.

    A platform admin is either a Django superuser (is_superuser=True) or
    has an active PLATFORM_ADMIN membership.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return TenantMembership.objects.filter(
        user=user,
        role=TenantRole.PLATFORM_ADMIN,
        is_active=True,
    ).exists()


def is_tenant_admin(user, tenant: Optional[Tenant] = None) -> bool:
    """Checks whether the user is an administrator for the specified tenant or platform-wide."""
    if is_platform_admin(user):
        return True
    if not user or not user.is_authenticated:
        return False

    qs = TenantMembership.objects.filter(
        user=user,
        role=TenantRole.TENANT_ADMIN,
        is_active=True,
    )
    if tenant:
        qs = qs.filter(tenant=tenant)
    return qs.exists()


def is_tenant_staff(user, tenant: Optional[Tenant] = None) -> bool:
    """Checks whether the user holds staff or administrator membership for the tenant."""
    if is_platform_admin(user):
        return True
    if not user or not user.is_authenticated:
        return False

    qs = TenantMembership.objects.filter(
        user=user,
        is_active=True,
    )
    if tenant:
        qs = qs.filter(tenant=tenant)
    return qs.exists()


def can_switch_tenant(user) -> bool:
    """Explicit capability check: ONLY platform administrators may switch tenant context."""
    return is_platform_admin(user)


def get_user_tenants(user):
    """Returns the QuerySet of Tenant instances the user is authorized to access."""
    if not user or not user.is_authenticated:
        return Tenant.objects.none()

    if is_platform_admin(user):
        return Tenant.objects.filter(is_active=True)

    return Tenant.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        is_active=True,
    ).distinct()


def resolve_requested_tenant(
    user,
    requested_tenant_id_or_slug: Optional[Union[int, str]] = None,
) -> Tenant:
    """
    Centralized, authorization-aware tenant resolution engine.

    Architecture (Section 3):
    1. If user is Platform Admin:
       - May switch to any active tenant.
       - If requested_tenant_id is provided, resolve and return that Tenant.
       - If none requested, return user's first available tenant or first active platform tenant.
    2. If user is Normal Tenant User (Tenant Admin / Staff):
       - CANNOT switch tenants.
       - Resolve user's single authorized tenant via active TenantMembership.
       - If user has no active tenant membership:
           Raise PermissionDenied("No authorized tenant membership found.")
       - If requested_tenant_id_or_slug is provided AND does not match authorized tenant:
           Raise PermissionDenied("Cross-tenant access violation: Unauthorized tenant context.")
       - Return authorized tenant.
    """
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    # 1. Platform Administrator flow
    if is_platform_admin(user):
        if requested_tenant_id_or_slug is not None:
            if isinstance(requested_tenant_id_or_slug, int) or str(requested_tenant_id_or_slug).isdigit():
                target = Tenant.objects.filter(id=int(requested_tenant_id_or_slug), is_active=True).first()
            else:
                target = Tenant.objects.filter(slug=str(requested_tenant_id_or_slug), is_active=True).first()

            if target:
                return target

        # Default fallback for platform admin: first active tenant
        default_tenant = Tenant.objects.filter(is_active=True).order_by("name").first()
        if not default_tenant:
            raise Http404("No operational tenant found on the platform.")
        return default_tenant

    # 2. Normal Tenant User flow
    user_memberships = list(
        TenantMembership.objects.filter(user=user, is_active=True).select_related("tenant")
    )
    if not user_memberships:
        raise PermissionDenied("You do not have an active membership for any tenant organization.")

    # User's assigned tenant
    authorized_tenant = user_memberships[0].tenant
    if not authorized_tenant.is_active:
        raise PermissionDenied(f"Tenant organization '{authorized_tenant.name}' is currently inactive.")

    # If caller explicitly asked for a specific tenant, verify exact match
    if requested_tenant_id_or_slug is not None:
        matches = False
        if isinstance(requested_tenant_id_or_slug, int) or str(requested_tenant_id_or_slug).isdigit():
            matches = (authorized_tenant.id == int(requested_tenant_id_or_slug))
        else:
            matches = (authorized_tenant.slug == str(requested_tenant_id_or_slug))

        if not matches:
            raise PermissionDenied(
                "Cross-tenant access violation: You are not authorized to switch to or access another tenant."
            )

    return authorized_tenant


def enforce_tenant_object_access(obj: Any, tenant: Tenant) -> None:
    """Verifies that a tenant-owned business object belongs strictly to the active tenant.

    Raises Http404 if there is an ownership mismatch to prevent leaking object existence.
    """
    obj_tenant = getattr(obj, "tenant", None)
    obj_tenant_id = getattr(obj, "tenant_id", None) or getattr(obj_tenant, "id", None)

    if obj_tenant_id is None or obj_tenant_id != tenant.id:
        raise Http404("Requested resource not found within the active organization.")
