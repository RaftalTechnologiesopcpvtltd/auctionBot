from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from apps.tenants.models import Tenant
from apps.tenants.security import (
    resolve_requested_tenant,
    is_platform_admin,
    is_tenant_staff,
)


def dashboard_auth_required(view_func):
    """
    Decorator requiring user authentication, staff/admin permissions,
    and enforcing centralized, authorization-aware tenant resolution.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/dashboard/login/?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser or is_tenant_staff(request.user)):
            messages.error(request, "Access restricted: Staff or Administrator permissions required.")
            return redirect("/dashboard/login/")

        # Resolve active tenant through centralized authorization engine
        session_tenant_id = request.session.get("active_tenant_id")
        try:
            active_tenant = resolve_requested_tenant(request.user, session_tenant_id)
        except PermissionDenied:
            # Mismatched or unauthorized tenant in session: reset to user's assigned tenant
            try:
                active_tenant = resolve_requested_tenant(request.user, None)
                request.session["active_tenant_id"] = active_tenant.id
            except PermissionDenied:
                messages.error(request, "You are not assigned to any active organization.")
                return redirect("/dashboard/login/")

        request.tenant = active_tenant
        if active_tenant:
            request.session["active_tenant_id"] = active_tenant.id
        request.is_platform_admin = is_platform_admin(request.user)

        return view_func(request, *args, **kwargs)

    return _wrapped_view
